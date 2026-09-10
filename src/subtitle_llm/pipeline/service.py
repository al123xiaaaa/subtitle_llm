from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.io import SubtitleIO
from subtitle_llm.llm import ChatClient, create_chat_client
from subtitle_llm.llm.token_counter import build_token_encoder
from subtitle_llm.pipeline.checkpoint import file_fingerprint, sidecar_path
from subtitle_llm.pipeline.chunk_acceptance import ChunkAcceptance
from subtitle_llm.pipeline.chunk_translator import ChunkTranslator
from subtitle_llm.pipeline.chunks import ChunkPlanner
from subtitle_llm.pipeline.chunk_processing import ChunkProcessingCoordinator
from subtitle_llm.pipeline.context import ContextService
from subtitle_llm.pipeline.llm_trace import LlmTraceRecorder
from subtitle_llm.pipeline.lifecycle import (
    TranslationTaskLifecycle,
    TranslationTaskLifecycleEvent,
)
from subtitle_llm.pipeline.lifecycle.projectors import TaskLifecycleProjector
from subtitle_llm.pipeline.normalization import (
    NormalizationOptions,
    normalize_subtitle,
    write_normalization_map,
)
from subtitle_llm.pipeline.quality import QualityGate
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.run_ledger import RunLedger
from subtitle_llm.pipeline.semantic_units import (
    SemanticUnit,
    build_semantic_units,
    semantic_entries,
)
from subtitle_llm.pipeline.source_corrections import (
    source_corrections_from_context,
    subtitle_with_source_display_corrections,
)
from subtitle_llm.pipeline.task_store import (
    TranslationTaskRecord,
    TranslationTaskStore,
    config_from_snapshot,
)
from subtitle_llm.progress_contract import ProgressContract
from subtitle_llm.progress_events import ProgressEmitter
from subtitle_llm.review import AutoReviewPort, ReviewPort, TuiReviewPort
from subtitle_llm.settings import AppConfig

logger = logging.getLogger(__name__)


@dataclass
class TranslationRequest:
    input_file: str | None
    output_file: str | None
    target_language: str
    source_language: str = "en"
    output_format: str | None = None
    resume: bool = False
    review_mode: str | None = None
    refine_translation: bool | None = None
    force_asr: bool = False
    task_id: str | None = None


@dataclass
class TranslationResult:
    subtitle: Subtitle
    report: TranslationReport


@dataclass(frozen=True)
class ResolvedInput:
    subtitle_file: str
    video_file: str | None = None


class TranslationService:
    """任务级编排：输入解析、任务记录、规范化、上下文、规划、写出。

    片段级编排（质量诊断、修复重译、复核）在 chunk_acceptance.ChunkAcceptance。
    """

    def __init__(
        self,
        config: AppConfig,
        translation_client: ChatClient | None = None,
        summary_client: ChatClient | None = None,
        review_port: ReviewPort | None = None,
        task_store: TranslationTaskStore | None = None,
    ):
        self.config = config
        self._translation_client_injected = translation_client is not None
        self._summary_client_injected = summary_client is not None
        self.translation_client = translation_client or create_chat_client(config.translation_model)
        self.summary_client = summary_client or create_chat_client(config.summary_model)
        self.review_port = review_port
        self.task_store = task_store or TranslationTaskStore()

    def translate(
        self,
        request: TranslationRequest,
        progress: ProgressEmitter | None = None,
        emit_complete: bool = True,
    ) -> TranslationResult:
        task_record = self._prepare_task_record_request(request)
        if task_record is not None:
            request = self._request_from_task_record(request, task_record)
            self._apply_task_config_snapshot(task_record)
        if not request.input_file:
            raise ValueError("input_file is required unless resuming with task_id")

        progress = progress or ProgressEmitter("translate")
        progress_contract = ProgressContract(progress)
        task_lifecycle = TranslationTaskLifecycle().apply(TranslationTaskLifecycleEvent.PREPARE_INPUT_STARTED)
        task_projector: TaskLifecycleProjector | None = None
        progress_contract.task_prepared()
        logger.info(
            "翻译任务开始: input=%s target_language=%s source_language=%s resume=%s review_mode=%s refine_translation=%s force_asr=%s",
            request.input_file,
            request.target_language,
            request.source_language,
            request.resume,
            request.review_mode or self.config.pipeline.review_mode,
            self._refine_translation_enabled(request),
            request.force_asr,
        )
        resolved_input = self._resolve_input(
            request.input_file,
            request.source_language,
            progress_contract,
            force_asr=request.force_asr,
        )
        input_file = resolved_input.subtitle_file
        output_file = request.output_file or self._default_output_file(
            input_file,
            request.target_language,
            request.source_language,
        )
        progress_contract.output_resolved(output_file)
        output_format = request.output_format or self.config.default_output_format
        context_file = sidecar_path(output_file, "_context.txt")
        report = TranslationReport(
            input_file=str(input_file),
            output_file=output_file,
            context_file=str(context_file),
            target_language=request.target_language,
            output_format=output_format,
            source_video_file=resolved_input.video_file,
        )
        trace_recorder = LlmTraceRecorder.for_run(output_file)
        report.llm_trace_dir = str(trace_recorder.trace_dir)
        progress_contract.diagnostics_prepared(trace_recorder.trace_dir)
        logger.info("LLM诊断目录已准备: %s", trace_recorder.trace_dir)
        logger.info(
            "翻译文件已准备: resolved_input=%s output=%s context=%s",
            input_file,
            output_file,
            context_file,
        )

        progress_contract.subtitle_reading(input_file)
        subtitle = SubtitleIO.read(
            input_file,
            max_chars=self.config.pipeline.max_chars,
            max_duration=self.config.pipeline.max_duration,
        )
        report.total_entries = len(subtitle.entries)
        progress_contract.subtitle_read(report.total_entries)
        logger.info("字幕读取完成: entries=%s", report.total_entries)

        progress_contract.normalization_started()
        normalization = normalize_subtitle(
            subtitle,
            NormalizationOptions(
                mode=self.config.pipeline.normalize_subtitles,
                max_cue_chars=self.config.pipeline.normalize_max_cue_chars,
                max_line_chars=self.config.pipeline.normalize_max_line_chars,
                max_duration_seconds=self.config.pipeline.normalize_max_duration,
                min_duration_seconds=self.config.pipeline.normalize_min_duration,
                sentence_language=request.source_language,
            ),
        )
        report.normalization_applied = normalization.applied
        report.normalization_reason = normalization.reason
        report.normalization_stats = normalization.stats.to_dict()
        task_input_file = input_file
        if normalization.applied:
            normalized_source_file = self._normalized_source_file(output_file, input_file, request.source_language)
            normalization_map_file = sidecar_path(output_file, "_normalization_map.json")
            SubtitleIO.write_srt(normalization.subtitle, normalized_source_file, output_format="source-only")
            write_normalization_map(normalization, normalization_map_file)
            subtitle = normalization.subtitle
            report.normalized_source_file = str(normalized_source_file)
            report.normalization_map_file = str(normalization_map_file)
            task_input_file = str(normalized_source_file)
            report.total_entries = len(subtitle.entries)
            progress_contract.normalization_done(
                original_entries=normalization.stats.original_entries,
                normalized_entries=len(subtitle.entries),
            )
            logger.info(
                "字幕规范化完成: input_entries=%s normalized_entries=%s normalized_file=%s map_file=%s stats=%s",
                normalization.stats.original_entries,
                len(subtitle.entries),
                normalized_source_file,
                normalization_map_file,
                normalization.stats.to_dict(),
            )
        else:
            progress_contract.normalization_skipped(normalization.reason)
            logger.info(
                "字幕规范化跳过: reason=%s stats=%s",
                normalization.reason,
                normalization.stats.to_dict(),
            )

        normalized_input_fingerprint = file_fingerprint(task_input_file)
        task_lifecycle = task_lifecycle.apply(TranslationTaskLifecycleEvent.PREPARE_INPUT_COMPLETED)
        output_file_for_record = self._absolute_path(output_file)
        should_restore = False
        if task_record is None and request.resume:
            task_record = self.task_store.find_resume_task(
                normalized_input_fingerprint=normalized_input_fingerprint,
                target_language=request.target_language,
                output_format=output_format,
                output_file=output_file_for_record,
            )
            should_restore = task_record is not None
        elif task_record is not None:
            self.task_store.validate_task(
                task_record,
                normalized_input_fingerprint=normalized_input_fingerprint,
                target_language=request.target_language,
                output_format=output_format,
                output_file=output_file_for_record,
            )
            should_restore = request.resume

        if task_record is None:
            task_record = self.task_store.create_task(
                input_display=request.input_file,
                working_directory=os.getcwd(),
                source_subtitle_path=self._absolute_path(input_file),
                normalized_input_fingerprint=normalized_input_fingerprint,
                target_language=request.target_language,
                source_language=request.source_language,
                output_format=output_format,
                output_file=output_file_for_record,
                config=self.config,
                context_file=str(context_file),
                llm_trace_dir=str(trace_recorder.trace_dir),
                source_video_file=resolved_input.video_file,
                source_url=request.input_file if self._is_url(request.input_file) else None,
                status=task_lifecycle.state,
            )
        report.task_id = task_record.task_id
        report.task_db_file = str(self.task_store.db_path)
        task_projector = TaskLifecycleProjector(self.task_store, task_record.task_id, report, progress_contract)
        task_projector.project(task_lifecycle.state)

        task_state_restore = RunLedger.restore_task_state(
            resume=should_restore,
            task_id=task_record.task_id,
            subtitle=subtitle,
            task_store=self.task_store,
            report=report,
        )
        resumed_indices = task_state_restore.resumed_indices
        run_ledger = task_state_restore.ledger
        run_ledger.save_task_state(self.task_store, task_record.task_id, subtitle, report, [])
        progress_contract.task_record_restored(len(resumed_indices))
        if resumed_indices:
            logger.info(
                "翻译任务记录恢复完成: task_id=%s resumed_entries=%s",
                task_record.task_id,
                len(resumed_indices),
            )

        try:
            progress_contract.context_generating(self.config.summary_model)
            context_service = ContextService(
                self.summary_client,
                self.config.summary_model,
                review_enabled=self.config.pipeline.context_review,
                trace_recorder=trace_recorder,
            )
            context_source = "\n".join(
                [entry.original_text for entry in subtitle.entries if len(entry.original_text) >= 10]
            )
            context, context_usage = context_service.build_context(context_source, request.target_language)
            report.token_usage.add_usage(context_usage.to_dict())
            context_service.save_context(context, context_file)
            progress_contract.context_generated(
                context_file=context_file,
                model=self.config.summary_model,
                usage=context_usage,
            )
            logger.info(
                "上下文生成完成: context_file=%s context_tokens=%s",
                context_file,
                context_usage.total_tokens,
            )
            run_ledger.save_task_state(self.task_store, task_record.task_id, subtitle, report, [])
        except Exception as exc:
            task_lifecycle = task_lifecycle.apply(TranslationTaskLifecycleEvent.FAILED)
            task_projector.project(task_lifecycle.state, error_summary=str(exc))
            raise

        review_mode = request.review_mode or self.config.pipeline.review_mode
        refine_translation = self._refine_translation_enabled(request)
        semantic_units_list = build_semantic_units(
            subtitle.entries,
            max_cues_per_unit=self.config.pipeline.semantic_max_cues_per_unit,
        )
        use_semantic_translation = self._use_semantic_translation(review_mode, semantic_units_list)
        translation_entries = subtitle.entries
        planner_resumed_indices = resumed_indices
        semantic_unit_by_index: dict[int, SemanticUnit] = {}
        if use_semantic_translation:
            translation_entries = semantic_entries(semantic_units_list)
            semantic_unit_by_index = {unit.index: unit for unit in semantic_units_list}
            planner_resumed_indices = {
                unit.index
                for unit in semantic_units_list
                if unit.entries and all(entry.index in resumed_indices for entry in unit.entries)
            }
            report.semantic_translation_applied = True
            report.semantic_units = len(semantic_units_list)
            report.semantic_multi_cue_units = len([unit for unit in semantic_units_list if len(unit.entries) > 1])
            progress_contract.semantic_units_planned(
                total_units=report.semantic_units,
                multi_cue_units=report.semantic_multi_cue_units,
            )
            logger.info(
                "语义翻译单元已启用: units=%s multi_cue_units=%s",
                report.semantic_units,
                report.semantic_multi_cue_units,
            )
        else:
            report.semantic_units = len(semantic_units_list)
            report.semantic_multi_cue_units = len([unit for unit in semantic_units_list if len(unit.entries) > 1])
            progress_contract.semantic_units_skipped()

        # 语义模式下，translation_entries 是单元级条目，但 LLM 按底层 cue 数输出。
        # token 预算必须按底层 cue 文本估算，否则会低估（1.2-1.6 倍）导致大 chunk
        # 通过预算检查却仍被截断。
        output_text_resolver = None
        if use_semantic_translation:
            def resolve_cue_texts(entry: SubtitleEntry) -> list[str]:
                unit = semantic_unit_by_index.get(entry.index)
                if unit:
                    return [e.original_text for e in unit.entries]
                return [entry.original_text]

            output_text_resolver = resolve_cue_texts

        planner = ChunkPlanner(
            chunk_size=self.config.pipeline.chunk_size,
            context_window_size=self.config.pipeline.context_window_size,
            ignore_subtitle_length=self.config.pipeline.ignore_subtitle_length,
            max_output_tokens=self._chunk_output_token_budget(),
            encoder=build_token_encoder(self.config.translation_model.model),
            output_text_resolver=output_text_resolver,
        )
        planned_chunks = planner.plan(translation_entries, resumed_indices=planner_resumed_indices)
        report.total_chunks = len(planned_chunks)
        report.short_entries = len(
            [
                entry
                for entry in translation_entries
                if len(entry.original_text.strip()) <= self.config.pipeline.ignore_subtitle_length
                and entry.index not in planner_resumed_indices
            ]
        )
        boundary_risks_by_key: dict[tuple[int, int], dict] = {}
        for planned in planned_chunks:
            for risk in planned.boundary_risks:
                boundary_risks_by_key[(risk["before_index"], risk["after_index"])] = risk
        report.boundary_risks = list(boundary_risks_by_key.values())
        report.boundary_risk_count = len(report.boundary_risks)
        progress_contract.chunks_planned(total_chunks=report.total_chunks, short_entries=report.short_entries)
        logger.info(
            "chunk规划完成: chunks=%s short_entries=%s boundary_risks=%s",
            report.total_chunks,
            report.short_entries,
            report.boundary_risk_count,
        )
        task_lifecycle = task_lifecycle.apply(TranslationTaskLifecycleEvent.PREPARE_TRANSLATION_COMPLETED)
        task_projector.project(task_lifecycle.state)

        translator = ChunkTranslator(
            self.translation_client,
            self.config.translation_model,
            trace_recorder=trace_recorder,
            total_chunks=report.total_chunks,
            progress=progress,
        )
        quality_gate = QualityGate()
        review_port = self._review_port(review_mode)
        progress_contract.chunk_pool_started(total_chunks=report.total_chunks, threads=self.config.pipeline.threads)
        logger.info("审核模式: %s", review_mode)
        translated_entries: list[SubtitleEntry] = [
            entry for entry in subtitle.entries if entry.index in resumed_indices
        ]
        acceptance = ChunkAcceptance(
            config=self.config,
            translator=translator,
            quality_gate=quality_gate,
            review_port=review_port,
            context=context,
            target_language=request.target_language,
            report=report,
            run_ledger=run_ledger,
            refine_translation=refine_translation,
        )
        chunk_processor = ChunkProcessingCoordinator(
            threads=self.config.pipeline.threads,
            semantic_output_granularity=self.config.pipeline.semantic_output_granularity,
        )
        try:
            if use_semantic_translation:
                chunk_processor.run_semantic_chunks(
                    planned_chunks,
                    semantic_unit_by_index,
                    translator,
                    acceptance,
                    subtitle,
                    translated_entries,
                    run_ledger,
                    self.task_store,
                    task_record.task_id,
                    report,
                )
            else:
                chunk_processor.run_chunks(
                    planned_chunks,
                    translator,
                    acceptance,
                    subtitle,
                    translated_entries,
                    run_ledger,
                    self.task_store,
                    task_record.task_id,
                    report,
                )

            task_lifecycle = task_lifecycle.apply(TranslationTaskLifecycleEvent.PROCESS_CHUNKS_COMPLETED)
            task_projector.project(task_lifecycle.state, emit_progress=True)
            run_ledger.finalize_subtitle(subtitle, translated_entries, report)
            output_subtitle, source_display_corrections = subtitle_with_source_display_corrections(
                subtitle,
                source_corrections_from_context(context),
            )
            if source_display_corrections:
                logger.info("写出字幕时应用源文展示修正: entries=%s", source_display_corrections)
            progress_contract.writing_srt(output_file)
            SubtitleIO.write_srt(output_subtitle, output_file, output_format=output_format)
            progress_contract.srt_written(output_file)
            completion_event = (
                TranslationTaskLifecycleEvent.FINALIZE_OUTPUT_COMPLETED_WITH_WARNINGS
                if translation_completed_with_warnings(report, subtitle)
                else TranslationTaskLifecycleEvent.FINALIZE_OUTPUT_COMPLETED
            )
            task_lifecycle = task_lifecycle.apply(completion_event)
            task_projector.project(
                task_lifecycle.state,
                total_chunks=report.total_chunks,
                emit_progress=emit_complete,
            )
            run_ledger.save_task_state(self.task_store, task_record.task_id, subtitle, report, translated_entries)
            logger.info(
                "翻译任务完成: output=%s failed_chunks=%s total_tokens=%s",
                output_file,
                len(report.failed_chunks),
                report.token_usage.total_tokens,
            )
            return TranslationResult(subtitle=subtitle, report=report)
        except Exception as exc:
            task_lifecycle = task_lifecycle.apply(TranslationTaskLifecycleEvent.FAILED)
            task_projector.project(task_lifecycle.state, error_summary=str(exc))
            raise
        finally:
            stop_review = getattr(review_port, "stop", None)
            if callable(stop_review):
                stop_review()

    def _prepare_task_record_request(self, request: TranslationRequest) -> TranslationTaskRecord | None:
        if not request.task_id:
            return None
        if not request.resume:
            raise ValueError("--task-id 只能和 --resume 一起使用")
        if request.input_file or request.output_file:
            raise ValueError("--task-id 恢复不能同时指定新的 --input 或 --output")
        return self.task_store.get_task(request.task_id)

    def _request_from_task_record(
        self,
        request: TranslationRequest,
        record: TranslationTaskRecord,
    ) -> TranslationRequest:
        return replace(
            request,
            input_file=record.source_subtitle_path,
            output_file=record.output_file,
            target_language=record.target_language,
            source_language=record.source_language,
            output_format=record.output_format,
        )

    def _apply_task_config_snapshot(self, record: TranslationTaskRecord) -> None:
        config = config_from_snapshot(record.config_snapshot_json)
        self.config = config
        if not self._translation_client_injected:
            self.translation_client = create_chat_client(config.translation_model)
        if not self._summary_client_injected:
            self.summary_client = create_chat_client(config.summary_model)

    def _absolute_path(self, file_path: str | Path) -> str:
        path = Path(file_path).expanduser()
        if path.is_absolute():
            return str(path)
        return str((Path(os.getcwd()) / path).resolve())

    def _use_semantic_translation(self, _review_mode: str, units: list[SemanticUnit]) -> bool:
        mode = self.config.pipeline.semantic_translation
        if mode == "off":
            return False
        if mode == "always":
            return bool(units)
        return any(len(unit.entries) > 1 for unit in units)

    def _chunk_output_token_budget(self) -> int:
        """每个 chunk 的输出 token 预算：max_tokens × 0.8，留 20% 余量防 JSON 截断。

        让 chunk 规划感知模型输出上限，避免单个 chunk 的翻译 JSON 超过 max_tokens
        被强制截断（表现为「Unterminated string」解析失败、整片回退原文）。
        """
        return int(self.config.translation_model.max_tokens * 0.8)

    def _refine_translation_enabled(self, request: TranslationRequest) -> bool:
        if request.refine_translation is not None:
            return request.refine_translation
        return self.config.pipeline.refine_translation

    def _review_port(self, review_mode: str) -> ReviewPort:
        if self.review_port:
            return self.review_port
        if review_mode == "tui":
            return TuiReviewPort()
        return AutoReviewPort()

    def _resolve_input(
        self,
        input_file: str,
        source_language: str,
        progress: ProgressContract,
        *,
        force_asr: bool = False,
    ) -> ResolvedInput:
        if not self._is_url(input_file):
            progress.local_input_selected(input_file)
            return ResolvedInput(subtitle_file=input_file)

        from subtitle_llm.media import download, transcribe

        progress.url_input_detected()
        logger.info(
            "检测到URL输入，准备下载或复用媒体: url=%s source_language=%s force_asr=%s",
            input_file,
            source_language,
            force_asr,
        )
        output_dir = Path.cwd() / "data" / "input"
        result = download(
            input_file,
            output_dir,
            source_language,
            progress=progress.emitter,
            force_asr=force_asr,
        )
        if len(result) == 3:
            _video_path, subtitle_path, audio_path = result
        else:
            _video_path, subtitle_path = result
            audio_path = None

        if subtitle_path:
            progress.subtitle_ready(subtitle_path)
            logger.info("URL输入解析到字幕: subtitle=%s video=%s", subtitle_path, _video_path)
            return ResolvedInput(subtitle_file=subtitle_path, video_file=_video_path)
        if not audio_path:
            raise RuntimeError("未找到字幕且无法提取音频")

        srt_path = output_dir / f"{Path(audio_path).stem}.srt"
        logger.info("URL输入未找到字幕，准备ASR转写: audio=%s output=%s", audio_path, srt_path)
        transcribed_path = transcribe(audio_path, source_language, srt_path, self.config.asr, progress=progress.emitter)
        progress.asr_ready(transcribed_path)
        logger.info("ASR转写完成: srt=%s", transcribed_path)
        return ResolvedInput(subtitle_file=transcribed_path, video_file=_video_path)

    def _is_url(self, value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    def _default_output_file(self, input_file: str, target_language: str, source_language: str) -> str:
        input_path = Path(input_file)
        title = input_path.stem
        source_code = self._language_code(source_language)
        if title.lower().endswith(f".{source_code}"):
            title = title[: -(len(source_code) + 1)]

        target_code = self._language_code(target_language)
        return str(Path("data") / "output" / f"{title}.{target_code}.srt")

    def _normalized_source_file(self, output_file: str | Path, input_file: str | Path, source_language: str) -> Path:
        output_path = Path(output_file)
        input_stem = Path(input_file).stem
        source_code = self._language_code(source_language)
        if input_stem.lower().endswith(f".{source_code}"):
            input_stem = input_stem[: -(len(source_code) + 1)]
        return output_path.with_name(f"{input_stem}.normalized.{source_code}.srt")

    def _language_code(self, language: str) -> str:
        mapping = {
            "chinese": "zh",
            "english": "en",
            "japanese": "ja",
            "korean": "ko",
            "french": "fr",
            "german": "de",
            "spanish": "es",
            "italian": "it",
            "portuguese": "pt",
            "russian": "ru",
            "cantonese": "yue",
        }
        normalized = language.strip().lower()
        return mapping.get(normalized, normalized[:2] or "translated")


def translation_completed_with_warnings(report: TranslationReport, subtitle: Subtitle) -> bool:
    return bool(report.failed_chunks or any(entry.needs_retranslation for entry in subtitle.entries))
