from __future__ import annotations

import concurrent.futures
import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.io import SubtitleIO
from subtitle_llm.llm import ChatClient, create_chat_client
from subtitle_llm.llm.types import CompletionUsage
from subtitle_llm.pipeline.checkpoint import CheckpointStore, file_fingerprint, sidecar_path
from subtitle_llm.pipeline.chunk_translator import ChunkTranslationResult, ChunkTranslator
from subtitle_llm.pipeline.chunks import ChunkPlanner, PlannedChunk
from subtitle_llm.pipeline.context import ContextService
from subtitle_llm.pipeline.llm_trace import LlmTraceRecorder
from subtitle_llm.pipeline.normalization import (
    NormalizationOptions,
    normalize_subtitle,
    write_normalization_map,
)
from subtitle_llm.pipeline.quality import QualityGate
from subtitle_llm.pipeline.report import AutoLayoutRepair, TranslationReport
from subtitle_llm.pipeline.semantic_units import (
    SemanticUnit,
    apply_semantic_translation,
    build_semantic_units,
    is_orphan_punctuation,
    make_unit,
    semantic_entries,
)
from subtitle_llm.pipeline.text import parse_translation_results
from subtitle_llm.progress_events import ProgressEmitter, chunk_payload
from subtitle_llm.review import AutoReviewPort, ReviewPort, ReviewResult, TuiReviewPort
from subtitle_llm.settings import AppConfig

logger = logging.getLogger(__name__)


@dataclass
class TranslationRequest:
    input_file: str
    output_file: str | None
    target_language: str
    source_language: str = "en"
    output_format: str | None = None
    resume: bool = False
    review_mode: str | None = None
    refine_translation: bool | None = None


@dataclass
class TranslationResult:
    subtitle: Subtitle
    report: TranslationReport


@dataclass
class TuiReviewOutcome:
    retranslated: bool
    trace_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResolvedInput:
    subtitle_file: str
    video_file: str | None = None


class TranslationService:
    def __init__(
        self,
        config: AppConfig,
        translation_client: ChatClient | None = None,
        summary_client: ChatClient | None = None,
        review_port: ReviewPort | None = None,
    ):
        self.config = config
        self.translation_client = translation_client or create_chat_client(config.translation_model)
        self.summary_client = summary_client or create_chat_client(config.summary_model)
        self.review_port = review_port

    def translate(
        self,
        request: TranslationRequest,
        progress: ProgressEmitter | None = None,
        emit_complete: bool = True,
    ) -> TranslationResult:
        progress = progress or ProgressEmitter("translate")
        progress.emit(
            stage="startup",
            detail="prepare_task",
            label="启动任务",
            message="正在准备翻译任务",
        )
        logger.info(
            "翻译任务开始: input=%s target_language=%s source_language=%s resume=%s review_mode=%s refine_translation=%s",
            request.input_file,
            request.target_language,
            request.source_language,
            request.resume,
            request.review_mode or self.config.pipeline.review_mode,
            self._refine_translation_enabled(request),
        )
        resolved_input = self._resolve_input(request.input_file, request.source_language, progress)
        input_file = resolved_input.subtitle_file
        output_file = request.output_file or self._default_output_file(
            input_file,
            request.target_language,
            request.source_language,
        )
        progress.emit(
            stage="prepare_translation",
            detail="resolve_output",
            label="确定输出路径",
            message=f"字幕输出将写入 {output_file}",
        )
        output_format = request.output_format or self.config.default_output_format
        checkpoint_file = sidecar_path(output_file, "_checkpoint.json")
        context_file = sidecar_path(output_file, "_context.txt")
        report = TranslationReport(
            input_file=str(input_file),
            output_file=output_file,
            checkpoint_file=str(checkpoint_file),
            context_file=str(context_file),
            output_format=output_format,
            source_video_file=resolved_input.video_file,
        )
        trace_recorder = LlmTraceRecorder.for_run(output_file)
        report.llm_trace_dir = str(trace_recorder.trace_dir)
        progress.emit(
            stage="startup",
            detail="prepare_diagnostics",
            label="准备诊断目录",
            message=f"LLM 诊断目录：{trace_recorder.trace_dir}",
        )
        logger.info("LLM诊断目录已准备: %s", trace_recorder.trace_dir)
        logger.info(
            "翻译文件已准备: resolved_input=%s output=%s checkpoint=%s context=%s",
            input_file,
            output_file,
            checkpoint_file,
            context_file,
        )

        progress.emit(
            stage="prepare_input",
            detail="read_subtitle",
            label="读取字幕",
            message=f"正在读取字幕：{input_file}",
        )
        subtitle = SubtitleIO.read(
            input_file,
            max_chars=self.config.pipeline.max_chars,
            max_duration=self.config.pipeline.max_duration,
        )
        report.total_entries = len(subtitle.entries)
        progress.emit(
            stage="prepare_input",
            detail="read_subtitle",
            status="done",
            label="读取字幕",
            message=f"已读取 {report.total_entries} 条字幕",
        )
        logger.info("字幕读取完成: entries=%s", report.total_entries)

        progress.emit(
            stage="prepare_input",
            detail="normalize_subtitle",
            label="规范化字幕",
            message="正在检查字幕是否需要重新断句和时间轴规范化",
        )
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
        checkpoint_input_file = input_file
        if normalization.applied:
            normalized_source_file = self._normalized_source_file(output_file, input_file, request.source_language)
            normalization_map_file = sidecar_path(output_file, "_normalization_map.json")
            SubtitleIO.write_srt(normalization.subtitle, normalized_source_file, output_format="source-only")
            write_normalization_map(normalization, normalization_map_file)
            subtitle = normalization.subtitle
            report.normalized_source_file = str(normalized_source_file)
            report.normalization_map_file = str(normalization_map_file)
            checkpoint_input_file = str(normalized_source_file)
            report.total_entries = len(subtitle.entries)
            progress.emit(
                stage="prepare_input",
                detail="normalize_subtitle",
                status="done",
                label="规范化字幕",
                message=(
                    f"已将 rolling caption 从 {normalization.stats.original_entries} 条"
                    f"规范化为 {len(subtitle.entries)} 条"
                ),
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
            progress.emit(
                stage="prepare_input",
                detail="normalize_subtitle",
                status="skipped",
                label="规范化字幕",
                message=f"无需规范化字幕：{normalization.reason}",
            )
            logger.info(
                "字幕规范化跳过: reason=%s stats=%s",
                normalization.reason,
                normalization.stats.to_dict(),
            )

        checkpoint = CheckpointStore(
            checkpoint_file=checkpoint_file,
            input_fingerprint=file_fingerprint(checkpoint_input_file),
            target_language=request.target_language,
            output_format=output_format,
            config_version=self.config.config_version,
        )
        resumed_indices, removed_entry_indices = self._restore_checkpoint(request, subtitle, checkpoint, report)
        progress.emit(
            stage="prepare_translation",
            detail="restore_checkpoint",
            status="done" if resumed_indices else "skipped",
            label="加载断点",
            message=f"从断点恢复 {len(resumed_indices)} 条字幕" if resumed_indices else "没有可恢复断点，本次从头处理",
        )
        if resumed_indices:
            logger.info("断点恢复完成: resumed_entries=%s", len(resumed_indices))

        progress.emit(
            stage="prepare_translation",
            detail="generate_context",
            label="生成上下文",
            message="正在生成全局摘要和术语上下文",
            model=self.config.summary_model,
        )
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
        progress.emit(
            stage="prepare_translation",
            detail="generate_context",
            status="done",
            label="生成上下文",
            message=f"上下文已写入 {context_file}",
            model=self.config.summary_model,
            usage=context_usage,
        )
        logger.info(
            "上下文生成完成: context_file=%s context_tokens=%s",
            context_file,
            context_usage.total_tokens,
        )

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
            progress.emit(
                stage="prepare_translation",
                detail="plan_semantic_units",
                status="done",
                label="构建语义单元",
                message=(
                    f"已构建 {report.semantic_units} 个语义单元，"
                    f"其中 {report.semantic_multi_cue_units} 个跨多条字幕"
                ),
            )
            logger.info(
                "语义翻译单元已启用: units=%s multi_cue_units=%s",
                report.semantic_units,
                report.semantic_multi_cue_units,
            )
        else:
            report.semantic_units = len(semantic_units_list)
            report.semantic_multi_cue_units = len([unit for unit in semantic_units_list if len(unit.entries) > 1])
            progress.emit(
                stage="prepare_translation",
                detail="plan_semantic_units",
                status="skipped",
                label="构建语义单元",
                message="当前任务继续使用逐字幕片段翻译",
            )

        planner = ChunkPlanner(
            chunk_size=self.config.pipeline.chunk_size,
            context_window_size=self.config.pipeline.context_window_size,
            ignore_subtitle_length=self.config.pipeline.ignore_subtitle_length,
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
        progress.emit(
            stage="prepare_translation",
            detail="plan_chunks",
            status="done",
            label="规划片段",
            message=f"已规划 {report.total_chunks} 个片段，短句保留 {report.short_entries} 条",
            total_chunks=report.total_chunks,
        )
        logger.info(
            "chunk规划完成: chunks=%s short_entries=%s boundary_risks=%s",
            report.total_chunks,
            report.short_entries,
            report.boundary_risk_count,
        )

        translator = ChunkTranslator(
            self.translation_client,
            self.config.translation_model,
            trace_recorder=trace_recorder,
            total_chunks=report.total_chunks,
            progress=progress,
        )
        quality_gate = QualityGate()
        review_port = self._review_port(review_mode)
        progress.emit(
            stage="processing_chunks",
            detail="start_chunk_pool",
            label="处理片段",
            message=f"开始并发处理 {report.total_chunks} 个片段，并发数 {self.config.pipeline.threads}",
            total_chunks=report.total_chunks,
        )
        logger.info("审核模式: %s", review_mode)
        translated_entries: list[SubtitleEntry] = [
            entry for entry in subtitle.entries if entry.index in resumed_indices
        ]
        try:
            if use_semantic_translation:
                self._run_semantic_chunks(
                    planned_chunks,
                    semantic_unit_by_index,
                    translator,
                    quality_gate,
                    review_port,
                    context,
                    request.target_language,
                    subtitle,
                    translated_entries,
                    removed_entry_indices,
                    checkpoint,
                    report,
                    refine_translation,
                )
            else:
                self._run_chunks(
                    planned_chunks,
                    translator,
                    quality_gate,
                    review_port,
                    context,
                    request.target_language,
                    subtitle,
                    translated_entries,
                    removed_entry_indices,
                    checkpoint,
                    report,
                    refine_translation,
                )

            progress.emit(
                stage="generate_result",
                detail="finalize_subtitle",
                label="汇总字幕",
                message="正在汇总所有字幕片段",
            )
            self._finalize_subtitle(subtitle, translated_entries, removed_entry_indices)
            report.stage = "完成"
            report.processed_entries = len(subtitle.entries)
            report.final_output_entries = len(subtitle.entries)
            self._save_checkpoint(checkpoint, subtitle, report, translated_entries, removed_entry_indices)
            progress.emit(
                stage="generate_result",
                detail="write_srt",
                label="写出字幕",
                message=f"正在写出字幕：{output_file}",
            )
            SubtitleIO.write_srt(subtitle, output_file, output_format=output_format)
            progress.emit(
                stage="generate_result",
                detail="write_srt",
                status="done",
                label="写出字幕",
                message=f"字幕已写出：{output_file}",
            )
            if emit_complete:
                progress.emit(
                    stage="complete",
                    detail="complete",
                    status="done",
                    label="完成",
                    message="翻译任务完成",
                    total_chunks=report.total_chunks,
                )
            logger.info(
                "翻译任务完成: output=%s failed_chunks=%s total_tokens=%s",
                output_file,
                len(report.failed_chunks),
                report.token_usage.total_tokens,
            )
            return TranslationResult(subtitle=subtitle, report=report)
        finally:
            stop_review = getattr(review_port, "stop", None)
            if callable(stop_review):
                stop_review()

    def _run_chunks(
        self,
        planned_chunks: list[PlannedChunk],
        translator: ChunkTranslator,
        quality_gate: QualityGate,
        review_port: ReviewPort,
        context: str,
        target_language: str,
        subtitle: Subtitle,
        translated_entries: list[SubtitleEntry],
        removed_entry_indices: set[int],
        checkpoint: CheckpointStore,
        report: TranslationReport,
        refine_translation: bool,
    ) -> None:
        done_futures: set[concurrent.futures.Future] = set()
        for planned in planned_chunks:
            report_event_chunk = chunk_payload(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                status="waiting",
                detail="waiting",
            )
            translator_progress(translator).emit(
                stage="processing_chunks",
                detail="queue_chunk",
                label="等待处理",
                message=f"片段 {planned.index + 1}/{report.total_chunks} 已加入队列",
                chunk=report_event_chunk,
                total_chunks=report.total_chunks,
            )
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.pipeline.threads) as executor:
            future_to_chunk = {
                executor.submit(
                    translator.translate_and_refine,
                    planned.entries,
                    context,
                    target_language,
                    planned.boundary_context,
                    planned.index,
                    refine_translation=refine_translation,
                ): planned
                for planned in planned_chunks
            }
            all_futures = set(future_to_chunk.keys())

            while len(done_futures) < len(all_futures):
                newly_done, _ = concurrent.futures.wait(
                    all_futures - done_futures,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in newly_done:
                    done_futures.add(future)
                    planned = future_to_chunk[future]
                    try:
                        result = future.result()
                        self._accept_chunk(
                            planned,
                            result,
                            translator,
                            quality_gate,
                            review_port,
                            context,
                            target_language,
                            translated_entries,
                            removed_entry_indices,
                            report,
                            refine_translation,
                        )
                    except Exception as exc:
                        logger.exception(
                            "chunk处理失败: chunk=%s entries=%s",
                            planned.index + 1,
                            [entry.index for entry in planned.entries],
                        )
                        self._handle_chunk_failure(planned, translated_entries, report, exc, translator.progress)
                    finally:
                        report.completed_chunks += 1
                        report.processed_entries = len({entry.index for entry in translated_entries})
                        self._save_checkpoint(checkpoint, subtitle, report, translated_entries, removed_entry_indices)
                        translator_progress(translator).emit(
                            stage="processing_chunks",
                            detail="checkpoint",
                            status="done",
                            label="保存断点",
                            message=f"已保存片段 {planned.index + 1}/{report.total_chunks} 的进度",
                            chunk=chunk_payload(
                                planned.entries,
                                chunk_index=planned.index,
                                total_chunks=report.total_chunks,
                                status="done" if not planned.entries or not any(entry.needs_retranslation for entry in planned.entries) else "warning",
                                detail="checkpoint",
                            ),
                            total_chunks=report.total_chunks,
                        )

    def _run_semantic_chunks(
        self,
        planned_chunks: list[PlannedChunk],
        semantic_unit_by_index: dict[int, SemanticUnit],
        translator: ChunkTranslator,
        quality_gate: QualityGate,
        review_port: ReviewPort,
        context: str,
        target_language: str,
        subtitle: Subtitle,
        translated_entries: list[SubtitleEntry],
        removed_entry_indices: set[int],
        checkpoint: CheckpointStore,
        report: TranslationReport,
        refine_translation: bool,
    ) -> None:
        done_futures: set[concurrent.futures.Future] = set()
        for planned in planned_chunks:
            translator_progress(translator).emit(
                stage="processing_chunks",
                detail="queue_chunk",
                label="等待处理",
                message=f"语义片段 {planned.index + 1}/{report.total_chunks} 已加入队列",
                chunk=chunk_payload(
                    planned.entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                    status="waiting",
                    detail="waiting",
                ),
                total_chunks=report.total_chunks,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.pipeline.threads) as executor:
            future_to_chunk = {
                executor.submit(
                    translator.translate_semantic_and_refine,
                    planned.entries,
                    context,
                    target_language,
                    planned.boundary_context,
                    planned.index,
                    refine_translation=refine_translation,
                ): planned
                for planned in planned_chunks
            }
            all_futures = set(future_to_chunk.keys())

            while len(done_futures) < len(all_futures):
                newly_done, _ = concurrent.futures.wait(
                    all_futures - done_futures,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in newly_done:
                    done_futures.add(future)
                    planned = future_to_chunk[future]
                    try:
                        result = future.result()
                        self._accept_semantic_chunk(
                            planned,
                            result,
                            semantic_unit_by_index,
                            translator,
                            quality_gate,
                            review_port,
                            context,
                            target_language,
                            translated_entries,
                            removed_entry_indices,
                            report,
                            refine_translation,
                        )
                    except Exception as exc:
                        logger.exception(
                            "语义chunk处理失败: chunk=%s units=%s",
                            planned.index + 1,
                            [entry.index for entry in planned.entries],
                        )
                        self._handle_semantic_chunk_failure(
                            planned,
                            semantic_unit_by_index,
                            translated_entries,
                            report,
                            exc,
                            translator.progress,
                        )
                    finally:
                        report.completed_chunks += 1
                        report.processed_entries = len({entry.index for entry in translated_entries})
                        self._save_checkpoint(checkpoint, subtitle, report, translated_entries, removed_entry_indices)
                        translator_progress(translator).emit(
                            stage="processing_chunks",
                            detail="checkpoint",
                            status="done",
                            label="保存断点",
                            message=f"已保存语义片段 {planned.index + 1}/{report.total_chunks} 的进度",
                            chunk=chunk_payload(
                                planned.entries,
                                chunk_index=planned.index,
                                total_chunks=report.total_chunks,
                                status="done"
                                if not planned.entries
                                or not any(entry.needs_retranslation for entry in planned.entries)
                                else "warning",
                                detail="checkpoint",
                            ),
                            total_chunks=report.total_chunks,
                        )

    def _accept_semantic_chunk(
        self,
        planned: PlannedChunk,
        result: ChunkTranslationResult,
        semantic_unit_by_index: dict[int, SemanticUnit],
        translator: ChunkTranslator,
        quality_gate: QualityGate,
        review_port: ReviewPort,
        context: str,
        target_language: str,
        translated_entries: list[SubtitleEntry],
        removed_entry_indices: set[int],
        report: TranslationReport,
        refine_translation: bool,
    ) -> None:
        report.token_usage.add_usage(result.usage.to_dict())
        translation = result.translation
        for entry, refined_text in parse_translation_results(translation, planned.entries):
            entry.set_translated_text(refined_text.strip())

        diagnosis = quality_gate.diagnose_chunk(
            planned.entries,
            translation=translation,
            target_language=target_language,
        )
        if translator.trace_recorder:
            translator.trace_recorder.update_quality(result.final_trace_id, diagnosis)
        quality_gate.apply_diagnosis(planned.entries, diagnosis)
        translator_progress(translator).emit(
            stage="processing_chunks",
            detail="quality",
            status="running",
            label="质量检查",
            message=chunk_quality_message(planned.index, report.total_chunks, diagnosis),
            chunk=chunk_payload(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                status=quality_chunk_status(diagnosis, AutoReviewPort()),
                detail="quality",
                issue_summary=diagnosis.summary if diagnosis.has_issues else "",
            ),
        )
        if diagnosis.has_issues:
            repair_usage = CompletionUsage()
            repaired_result = translator.repair_translation_traced(
                planned.entries,
                translation,
                target_language,
                usage=repair_usage,
                quality_report=diagnosis.to_prompt_report(),
                chunk_index=planned.index,
            )
            repaired = repaired_result.text
            report.token_usage.add_usage(repair_usage.to_dict())
            for entry, refined_text in parse_translation_results(repaired, planned.entries):
                entry.needs_retranslation = False
                entry.set_translated_text(refined_text.strip())
            repaired_diagnosis = quality_gate.diagnose_chunk(
                planned.entries,
                translation=repaired,
                target_language=target_language,
            )
            if translator.trace_recorder:
                translator.trace_recorder.update_quality(
                    repaired_result.trace_id,
                    repaired_diagnosis,
                )
            quality_gate.apply_diagnosis(planned.entries, repaired_diagnosis)
            diagnosis = repaired_diagnosis
            translator_progress(translator).emit(
                stage="processing_chunks",
                detail="quality",
                status="warning" if repaired_diagnosis.has_issues else "done",
                label="质量检查",
                message=chunk_quality_message(planned.index, report.total_chunks, repaired_diagnosis),
                chunk=chunk_payload(
                    planned.entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                    status="warning" if repaired_diagnosis.has_issues else "done",
                    detail="quality",
                    issue_summary=repaired_diagnosis.summary if repaired_diagnosis.has_issues else "",
                ),
            )

        source_entries: list[SubtitleEntry] = []
        semantic_units = [semantic_unit_by_index[semantic_entry.index] for semantic_entry in planned.entries]
        for semantic_entry, unit in zip(planned.entries, semantic_units, strict=True):
            source_entries.extend(apply_semantic_translation(
                unit,
                semantic_entry.translated_text,
                target_language=target_language,
            ))

        source_entries = self._repair_semantic_layout(
            planned,
            source_entries,
            semantic_units,
            removed_entry_indices,
            report,
            target_language,
        )
        source_diagnosis = quality_gate.diagnose_chunk(source_entries, target_language=target_language)
        quality_gate.apply_diagnosis(source_entries, source_diagnosis)
        if source_diagnosis.has_issues and not isinstance(review_port, AutoReviewPort):
            logger.warning(
                "语义chunk映射后质量诊断命中，进入TUI复核: chunk=%s reliability=%s flagged=%s summary=%s",
                planned.index + 1,
                source_diagnosis.reliability,
                source_diagnosis.flagged_entries,
                source_diagnosis.summary,
            )
            source_entries = self._review_semantic_chunk_with_tui(
                planned,
                source_entries,
                semantic_units,
                translator,
                quality_gate,
                review_port,
                context,
                target_language,
                removed_entry_indices,
                report,
                refine_translation,
            )

        translated_entries.extend(source_entries)

        final_status = "warning" if diagnosis.has_issues or any(entry.needs_retranslation for entry in source_entries) else "done"
        translator_progress(translator).emit(
            stage="processing_chunks",
            detail="accept_chunk",
            status=final_status,
            label="接受片段",
            message=f"语义片段 {planned.index + 1}/{report.total_chunks} 已接受并映射回字幕",
            chunk=chunk_payload(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                status=final_status,
                detail="accept_chunk",
            ),
        )
        logger.info("语义chunk接受完成: chunk=%s units=%s", planned.index + 1, len(planned.entries))

    def _review_semantic_chunk_with_tui(
        self,
        planned: PlannedChunk,
        source_entries: list[SubtitleEntry],
        semantic_units: list[SemanticUnit],
        translator: ChunkTranslator,
        quality_gate: QualityGate,
        review_port: ReviewPort,
        context: str,
        target_language: str,
        removed_entry_indices: set[int],
        report: TranslationReport,
        refine_translation: bool,
    ) -> list[SubtitleEntry]:
        max_rounds = 2
        cue_to_unit = {
            entry.index: unit
            for unit in semantic_units
            for entry in unit.entries
        }
        current_entries = source_entries
        outcome = TuiReviewOutcome(retranslated=False)
        diagnosis = quality_gate.diagnose_chunk(current_entries, target_language=target_language)

        for review_round in range(1, max_rounds + 1):
            translator_progress(translator).emit(
                stage="processing_chunks",
                detail="tui_wait",
                status="running",
                label="等待复核",
                message=f"语义片段 {planned.index + 1}/{report.total_chunks} 等待 TUI 复核",
                chunk=chunk_payload(
                    current_entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                    status="review",
                    detail="tui_wait",
                ),
            )
            review_result = review_port.review(
                current_entries,
                planned.index,
                report.total_chunks,
                completed_chunks=report.completed_chunks,
            )
            removed_entry_indices.update(review_result.removed_entry_indices)
            current_entries = review_result.chunk
            logger.info(
                "语义TUI审核完成: chunk=%s round=%s selected_for_retranslation=%s cascade_start=%s drift_start=%s removed=%s",
                planned.index + 1,
                review_round,
                len(review_result.entries_to_retranslate),
                review_result.cascade_start_index,
                review_result.alignment_drift_start_index,
                review_result.removed_entry_indices,
            )

            outcome = self._apply_semantic_tui_review_result(
                planned,
                current_entries,
                semantic_units,
                cue_to_unit,
                review_result,
                translator,
                context,
                target_language,
                report,
                refine_translation,
            )
            if not outcome.retranslated:
                for entry in current_entries:
                    entry.needs_retranslation = False
                translator_progress(translator).emit(
                    stage="processing_chunks",
                    detail="tui_accept",
                    status="done",
                    label="复核完成",
                    message=f"语义片段 {planned.index + 1}/{report.total_chunks} 已由用户接受",
                    chunk=chunk_payload(
                        current_entries,
                        chunk_index=planned.index,
                        total_chunks=report.total_chunks,
                        status="done",
                        detail="tui_accept",
                    ),
                )
                return current_entries

            current_entries = self._repair_semantic_layout(
                planned,
                current_entries,
                semantic_units,
                removed_entry_indices,
                report,
                target_language,
            )
            diagnosis = quality_gate.diagnose_chunk(current_entries, target_language=target_language)
            if translator.trace_recorder:
                for trace_id in outcome.trace_ids:
                    translator.trace_recorder.update_quality(trace_id, diagnosis)
            quality_gate.apply_diagnosis(current_entries, diagnosis)
            if not diagnosis.has_issues:
                translator_progress(translator).emit(
                    stage="processing_chunks",
                    detail="tui_quality",
                    status="done",
                    label="复核后质检",
                    message=f"语义片段 {planned.index + 1}/{report.total_chunks} 复核后通过质量检查",
                    chunk=chunk_payload(
                        current_entries,
                        chunk_index=planned.index,
                        total_chunks=report.total_chunks,
                        status="done",
                        detail="tui_quality",
                    ),
                )
                return current_entries

            logger.warning(
                "语义TUI重译后质量诊断仍命中: chunk=%s round=%s reliability=%s flagged=%s summary=%s",
                planned.index + 1,
                review_round,
                diagnosis.reliability,
                diagnosis.flagged_entries,
                diagnosis.summary,
            )

        if translator.trace_recorder:
            for trace_id in outcome.trace_ids:
                translator.trace_recorder.update_quality(trace_id, diagnosis, status="failed")
        translator_progress(translator).emit(
            stage="processing_chunks",
            detail="tui_warning",
            status="warning",
            label="带风险继续",
            message=f"语义片段 {planned.index + 1}/{report.total_chunks} 复核达到上限，带风险继续",
            chunk=chunk_payload(
                current_entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                status="warning",
                detail="tui_warning",
                issue_summary=diagnosis.summary,
            ),
        )
        return current_entries

    def _repair_semantic_layout(
        self,
        planned: PlannedChunk,
        source_entries: list[SubtitleEntry],
        semantic_units: list[SemanticUnit],
        removed_entry_indices: set[int],
        report: TranslationReport,
        target_language: str,
    ) -> list[SubtitleEntry]:
        source_by_index = {entry.index: entry for entry in source_entries}
        removed_in_chunk: set[int] = set()

        for unit in semantic_units:
            last_kept: SubtitleEntry | None = None
            for unit_entry in unit.entries:
                entry = source_by_index.get(unit_entry.index)
                if entry is None or entry.index in removed_in_chunk:
                    continue

                reason = semantic_layout_repair_reason(entry)
                if reason and last_kept is not None:
                    last_kept.end_time = entry.end_time
                    last_kept.original_text = join_non_empty_text(last_kept.original_text, entry.original_text)
                    last_kept.translated_text = join_translated_text(
                        last_kept.translated_text,
                        entry.translated_text,
                        target_language,
                    )
                    last_kept.needs_retranslation = last_kept.needs_retranslation or entry.needs_retranslation
                    removed_in_chunk.add(entry.index)
                    removed_entry_indices.add(entry.index)
                    repair = AutoLayoutRepair(
                        merged_index=last_kept.index,
                        removed_index=entry.index,
                        reason=reason,
                    )
                    report.auto_layout_repairs.append(repair)
                    logger.info(
                        "语义布局自动合并: chunk=%s semantic_unit=%s merged_index=%s removed_index=%s reason=%s",
                        planned.index + 1,
                        unit.index,
                        repair.merged_index,
                        repair.removed_index,
                        repair.reason,
                    )
                    continue

                last_kept = entry

        if not removed_in_chunk:
            return source_entries
        return [entry for entry in source_entries if entry.index not in removed_in_chunk]

    def _apply_semantic_tui_review_result(
        self,
        planned: PlannedChunk,
        current_entries: list[SubtitleEntry],
        semantic_units: list[SemanticUnit],
        cue_to_unit: dict[int, SemanticUnit],
        review_result: ReviewResult,
        translator: ChunkTranslator,
        context: str,
        target_language: str,
        report: TranslationReport,
        refine_translation: bool,
    ) -> TuiReviewOutcome:
        if review_result.alignment_drift_start_index is not None:
            return self._apply_semantic_alignment_drift_review_result(
                planned,
                current_entries,
                review_result.alignment_drift_start_index,
                translator,
                context,
                target_language,
                report,
            )

        affected_unit_indices = self._affected_semantic_unit_indices(
            semantic_units,
            cue_to_unit,
            review_result,
        )
        outcome = TuiReviewOutcome(retranslated=False)
        if not affected_unit_indices:
            return outcome

        current_by_index = {entry.index: entry for entry in current_entries}
        units_to_translate = [
            unit
            for unit in (
                self._current_semantic_unit(unit, current_by_index)
                for unit in semantic_units
                if unit.index in affected_unit_indices
            )
            if unit is not None
        ]
        if not units_to_translate:
            return outcome

        semantic_chunk = semantic_entries(units_to_translate)
        write_back_start = review_result.cascade_start_index
        write_back_indices = [
            entry.index
            for unit in units_to_translate
            for entry in unit.entries
            if write_back_start is None or entry.index >= write_back_start
        ]
        translator_progress(translator).emit(
            stage="processing_chunks",
            detail="tui_semantic",
            label="TUI 语义重译",
            message=(
                f"语义片段 {planned.index + 1}/{report.total_chunks} "
                f"正在按 {len(units_to_translate)} 个完整语义单元重译"
            ),
            chunk=chunk_payload(
                current_entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                status="repairing",
                detail="tui_semantic",
            ),
        )
        selected_result = translator.translate_semantic_and_refine(
            semantic_chunk,
            context,
            target_language,
            planned.boundary_context,
            planned.index,
            "tui-semantic",
            refine_translation=refine_translation,
        )
        report.token_usage.add_usage(selected_result.usage.to_dict())
        for semantic_entry, refined_text in parse_translation_results(
            selected_result.translation,
            selected_result.chunk,
        ):
            semantic_entry.set_translated_text(refined_text.strip())

        for semantic_entry, unit in zip(semantic_chunk, units_to_translate, strict=True):
            apply_semantic_translation(
                unit,
                semantic_entry.translated_text,
                target_language=target_language,
                write_back_from_index=write_back_start,
            )

        outcome.retranslated = True
        if selected_result.final_trace_id:
            outcome.trace_ids.append(selected_result.final_trace_id)
        logger.info(
            "TUI语义重译完成: chunk=%s semantic_context_units=%s cue_indices=%s cascade_start=%s write_back_indices=%s",
            planned.index + 1,
            len(units_to_translate),
            [unit.cue_indices for unit in units_to_translate],
            write_back_start,
            write_back_indices,
        )
        return outcome

    def _apply_semantic_alignment_drift_review_result(
        self,
        planned: PlannedChunk,
        current_entries: list[SubtitleEntry],
        drift_start: int,
        translator: ChunkTranslator,
        context: str,
        target_language: str,
        report: TranslationReport,
    ) -> TuiReviewOutcome:
        outcome = TuiReviewOutcome(retranslated=False)
        drift_position = self._entry_position(current_entries, drift_start)
        if drift_position is None:
            logger.warning("语义TUI漂移起点不存在，跳过漂移重译: chunk=%s drift_start=%s", planned.index + 1, drift_start)
            return outcome

        stable_anchors = current_entries[max(0, drift_position - 6):drift_position]
        drift_entries = current_entries[drift_position:]
        drift_usage = CompletionUsage()
        translator_progress(translator).emit(
            stage="processing_chunks",
            detail="drift",
            label="对齐漂移重译",
            message=f"语义片段 {planned.index + 1}/{report.total_chunks} 从字幕 {drift_start} 开始重译",
            chunk=chunk_payload(
                drift_entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                status="repairing",
                detail="drift",
            ),
        )
        drift_result = translator.retranslate_alignment_drift_traced(
            drift_entries,
            stable_anchors,
            context,
            target_language,
            planned.boundary_context,
            drift_usage,
            chunk_index=planned.index,
        )
        report.token_usage.add_usage(drift_usage.to_dict())
        for entry, refined_text in parse_translation_results(drift_result.text, drift_entries):
            entry.needs_retranslation = False
            entry.set_translated_text(refined_text.strip())

        logger.info(
            "语义TUI对齐漂移重译完成: chunk=%s drift_start=%s entries=%s anchors=%s write_back_indices=%s",
            planned.index + 1,
            drift_start,
            len(drift_entries),
            len(stable_anchors),
            [entry.index for entry in drift_entries],
        )
        outcome.retranslated = True
        if drift_result.trace_id:
            outcome.trace_ids.append(drift_result.trace_id)
        return outcome

    def _affected_semantic_unit_indices(
        self,
        semantic_units: list[SemanticUnit],
        cue_to_unit: dict[int, SemanticUnit],
        review_result: ReviewResult,
    ) -> set[int]:
        affected = {
            cue_to_unit[entry.index].index
            for entry in review_result.entries_to_retranslate
            if entry.index in cue_to_unit
        }
        cascade_start = review_result.cascade_start_index
        if cascade_start is not None:
            affected.update(
                unit.index
                for unit in semantic_units
                if unit.entries and unit.entries[-1].index >= cascade_start
            )
        return affected

    def _current_semantic_unit(
        self,
        unit: SemanticUnit,
        current_by_index: dict[int, SubtitleEntry],
    ) -> SemanticUnit | None:
        entries = [current_by_index[index] for index in unit.cue_indices if index in current_by_index]
        if not entries:
            return None
        return make_unit(unit.index, entries)

    def _accept_chunk(
        self,
        planned: PlannedChunk,
        result: ChunkTranslationResult,
        translator: ChunkTranslator,
        quality_gate: QualityGate,
        review_port: ReviewPort,
        context: str,
        target_language: str,
        translated_entries: list[SubtitleEntry],
        removed_entry_indices: set[int],
        report: TranslationReport,
        refine_translation: bool,
    ) -> None:
        report.token_usage.add_usage(result.usage.to_dict())
        translation = result.translation
        for entry, refined_text in parse_translation_results(translation, planned.entries):
            entry.set_translated_text(refined_text.strip())

        diagnosis = quality_gate.diagnose_chunk(
            planned.entries,
            translation=translation,
            target_language=target_language,
        )
        if translator.trace_recorder:
            translator.trace_recorder.update_quality(result.final_trace_id, diagnosis)
        quality_gate.apply_diagnosis(planned.entries, diagnosis)
        translator_progress(translator).emit(
            stage="processing_chunks",
            detail="quality",
            status="running",
            label="质量检查",
            message=chunk_quality_message(planned.index, report.total_chunks, diagnosis),
            chunk=chunk_payload(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                status=quality_chunk_status(diagnosis, review_port),
                detail="quality",
                issue_summary=diagnosis.summary if diagnosis.has_issues else "",
            ),
        )
        if diagnosis.has_issues:
            logger.warning(
                "chunk质量诊断命中: chunk=%s reliability=%s flagged=%s summary=%s",
                planned.index + 1,
                diagnosis.reliability,
                diagnosis.flagged_entries,
                diagnosis.summary,
            )
        if diagnosis.has_issues:
            if isinstance(review_port, AutoReviewPort):
                repair_usage = CompletionUsage()
                repaired_result = translator.repair_translation_traced(
                    planned.entries,
                    translation,
                    target_language,
                    usage=repair_usage,
                    quality_report=diagnosis.to_prompt_report(),
                    chunk_index=planned.index,
                )
                repaired = repaired_result.text
                report.token_usage.add_usage(repair_usage.to_dict())
                for entry, refined_text in parse_translation_results(repaired, planned.entries):
                    entry.needs_retranslation = False
                    entry.set_translated_text(refined_text.strip())
                repaired_diagnosis = quality_gate.diagnose_chunk(
                    planned.entries,
                    translation=repaired,
                    target_language=target_language,
                )
                if translator.trace_recorder:
                    translator.trace_recorder.update_quality(
                        repaired_result.trace_id,
                        repaired_diagnosis,
                    )
                quality_gate.apply_diagnosis(planned.entries, repaired_diagnosis)
                translator_progress(translator).emit(
                    stage="processing_chunks",
                    detail="quality",
                    status="warning" if repaired_diagnosis.has_issues else "done",
                    label="质量检查",
                    message=chunk_quality_message(planned.index, report.total_chunks, repaired_diagnosis),
                    chunk=chunk_payload(
                        planned.entries,
                        chunk_index=planned.index,
                        total_chunks=report.total_chunks,
                        status="warning" if repaired_diagnosis.has_issues else "done",
                        detail="quality",
                        issue_summary=repaired_diagnosis.summary if repaired_diagnosis.has_issues else "",
                    ),
                )
                logger.info(
                    "chunk自动重译完成: chunk=%s reliability=%s flagged=%s",
                    planned.index + 1,
                    repaired_diagnosis.reliability,
                    repaired_diagnosis.flagged_entries,
                )
            else:
                self._review_chunk_with_tui(
                    planned,
                    translator,
                    quality_gate,
                    review_port,
                    context,
                    target_language,
                    removed_entry_indices,
                    report,
                    refine_translation,
                )

        for entry in planned.entries:
            translated_entries.append(entry)
        final_status = "warning" if any(entry.needs_retranslation for entry in planned.entries) else "done"
        translator_progress(translator).emit(
            stage="processing_chunks",
            detail="accept_chunk",
            status=final_status,
            label="接受片段",
            message=f"片段 {planned.index + 1}/{report.total_chunks} 已接受",
            chunk=chunk_payload(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                status=final_status,
                detail="accept_chunk",
            ),
        )
        logger.info("chunk接受完成: chunk=%s entries=%s", planned.index + 1, len(planned.entries))

    def _review_chunk_with_tui(
        self,
        planned: PlannedChunk,
        translator: ChunkTranslator,
        quality_gate: QualityGate,
        review_port: ReviewPort,
        context: str,
        target_language: str,
        removed_entry_indices: set[int],
        report: TranslationReport,
        refine_translation: bool,
    ) -> None:
        max_rounds = 2
        for review_round in range(1, max_rounds + 1):
            translator_progress(translator).emit(
                stage="processing_chunks",
                detail="tui_wait",
                status="running",
                label="等待复核",
                message=f"片段 {planned.index + 1}/{report.total_chunks} 等待 TUI 复核",
                chunk=chunk_payload(
                    planned.entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                    status="review",
                    detail="tui_wait",
                ),
            )
            review_result = review_port.review(
                planned.entries,
                planned.index,
                report.total_chunks,
                completed_chunks=report.completed_chunks,
            )
            removed_entry_indices.update(review_result.removed_entry_indices)
            planned.entries = review_result.chunk
            logger.info(
                "TUI审核完成: chunk=%s round=%s selected_for_retranslation=%s cascade_start=%s drift_start=%s removed=%s",
                planned.index + 1,
                review_round,
                len(review_result.entries_to_retranslate),
                review_result.cascade_start_index,
                review_result.alignment_drift_start_index,
                review_result.removed_entry_indices,
            )

            outcome = self._apply_tui_review_result(
                planned,
                review_result,
                translator,
                context,
                target_language,
                report,
                refine_translation,
            )
            if not outcome.retranslated:
                for entry in planned.entries:
                    entry.needs_retranslation = False
                translator_progress(translator).emit(
                    stage="processing_chunks",
                    detail="tui_accept",
                    status="done",
                    label="复核完成",
                    message=f"片段 {planned.index + 1}/{report.total_chunks} 已由用户接受",
                    chunk=chunk_payload(
                        planned.entries,
                        chunk_index=planned.index,
                        total_chunks=report.total_chunks,
                        status="done",
                        detail="tui_accept",
                    ),
                )
                logger.info("TUI审核接受当前chunk: chunk=%s round=%s", planned.index + 1, review_round)
                return

            diagnosis = quality_gate.diagnose_chunk(planned.entries, target_language=target_language)
            if translator.trace_recorder:
                for trace_id in outcome.trace_ids:
                    translator.trace_recorder.update_quality(trace_id, diagnosis)
            quality_gate.apply_diagnosis(planned.entries, diagnosis)
            if not diagnosis.has_issues:
                translator_progress(translator).emit(
                    stage="processing_chunks",
                    detail="tui_quality",
                    status="done",
                    label="复核后质检",
                    message=f"片段 {planned.index + 1}/{report.total_chunks} 复核后通过质量检查",
                    chunk=chunk_payload(
                        planned.entries,
                        chunk_index=planned.index,
                        total_chunks=report.total_chunks,
                        status="done",
                        detail="tui_quality",
                    ),
                )
                logger.info("TUI重译后质量检查通过: chunk=%s round=%s", planned.index + 1, review_round)
                return

            logger.warning(
                "TUI重译后质量诊断仍命中: chunk=%s round=%s reliability=%s flagged=%s summary=%s",
                planned.index + 1,
                review_round,
                diagnosis.reliability,
                diagnosis.flagged_entries,
                diagnosis.summary,
            )

        if translator.trace_recorder:
            for trace_id in outcome.trace_ids:
                translator.trace_recorder.update_quality(trace_id, diagnosis, status="failed")
        translator_progress(translator).emit(
            stage="processing_chunks",
            detail="tui_warning",
            status="warning",
            label="带风险继续",
            message=f"片段 {planned.index + 1}/{report.total_chunks} 复核达到上限，带风险继续",
            chunk=chunk_payload(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                status="warning",
                detail="tui_warning",
                issue_summary=diagnosis.summary,
            ),
        )
        logger.warning("TUI复核达到上限，接受当前结果继续: chunk=%s", planned.index + 1)

    def _apply_tui_review_result(
        self,
        planned: PlannedChunk,
        review_result: ReviewResult,
        translator: ChunkTranslator,
        context: str,
        target_language: str,
        report: TranslationReport,
        refine_translation: bool,
    ) -> TuiReviewOutcome:
        drift_start = review_result.alignment_drift_start_index
        ordinary_indices = {entry.index for entry in review_result.entries_to_retranslate}
        ordinary_entries = [
            entry
            for entry in planned.entries
            if entry.index in ordinary_indices
            and not (drift_start is not None and entry.index >= drift_start)
        ]

        outcome = TuiReviewOutcome(retranslated=False)
        if ordinary_entries:
            translator_progress(translator).emit(
                stage="processing_chunks",
                detail="tui_ordinary",
                label="TUI 普通重译",
                message=f"片段 {planned.index + 1}/{report.total_chunks} 正在重译 {len(ordinary_entries)} 行",
                chunk=chunk_payload(
                    ordinary_entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                    status="repairing",
                    detail="tui_ordinary",
                ),
            )
            selected_result = translator.translate_and_refine(
                ordinary_entries,
                context,
                target_language,
                planned.boundary_context,
                planned.index,
                "tui-ordinary",
                refine_translation=refine_translation,
            )
            report.token_usage.add_usage(selected_result.usage.to_dict())
            for entry, refined_text in parse_translation_results(
                selected_result.translation,
                selected_result.chunk,
            ):
                entry.needs_retranslation = False
                entry.set_translated_text(refined_text.strip())
            logger.info(
                "TUI普通重译完成: chunk=%s selected=%s cascade_start=%s",
                planned.index + 1,
                len(ordinary_entries),
                review_result.cascade_start_index,
            )
            outcome.retranslated = True
            if selected_result.final_trace_id:
                outcome.trace_ids.append(selected_result.final_trace_id)

        if drift_start is not None:
            drift_position = self._entry_position(planned.entries, drift_start)
            if drift_position is None:
                logger.warning("TUI漂移起点不存在，跳过漂移重译: chunk=%s drift_start=%s", planned.index + 1, drift_start)
                return outcome

            stable_anchors = planned.entries[max(0, drift_position - 6):drift_position]
            drift_entries = planned.entries[drift_position:]
            drift_usage = CompletionUsage()
            translator_progress(translator).emit(
                stage="processing_chunks",
                detail="drift",
                label="对齐漂移重译",
                message=f"片段 {planned.index + 1}/{report.total_chunks} 从字幕 {drift_start} 开始重译",
                chunk=chunk_payload(
                    drift_entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                    status="repairing",
                    detail="drift",
                ),
            )
            drift_result = translator.retranslate_alignment_drift_traced(
                drift_entries,
                stable_anchors,
                context,
                target_language,
                planned.boundary_context,
                drift_usage,
                chunk_index=planned.index,
            )
            drift_translation = drift_result.text
            report.token_usage.add_usage(drift_usage.to_dict())
            for entry, refined_text in parse_translation_results(drift_translation, drift_entries):
                entry.needs_retranslation = False
                entry.set_translated_text(refined_text.strip())
            logger.info(
                "TUI对齐漂移重译完成: chunk=%s drift_start=%s entries=%s anchors=%s",
                planned.index + 1,
                drift_start,
                len(drift_entries),
                len(stable_anchors),
            )
            outcome.retranslated = True
            if drift_result.trace_id:
                outcome.trace_ids.append(drift_result.trace_id)

        return outcome

    def _entry_position(self, entries: list[SubtitleEntry], entry_index: int) -> int | None:
        for position, entry in enumerate(entries):
            if entry.index == entry_index:
                return position
        return None

    def _handle_chunk_failure(
        self,
        planned: PlannedChunk,
        translated_entries: list[SubtitleEntry],
        report: TranslationReport,
        exc: Exception,
        progress: ProgressEmitter | None,
    ) -> None:
        report.mark_failed(planned.index, [entry.index for entry in planned.entries], exc)
        if progress is not None:
            progress.emit(
                stage="processing_chunks",
                detail="chunk_failed",
                status="failed",
                label="片段失败",
                message=f"片段 {planned.index + 1} 处理失败：{exc}",
                chunk=chunk_payload(
                    planned.entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                    status="failed",
                    detail="chunk_failed",
                    issue_summary=str(exc),
                ),
            )
        if self.config.pipeline.fallback_on_chunk_error == "abort":
            logger.error("chunk失败且配置为中止: chunk=%s error=%s", planned.index + 1, exc)
            raise exc
        logger.warning("chunk失败后回退到原文: chunk=%s error=%s", planned.index + 1, exc)
        for entry in planned.entries:
            entry.needs_retranslation = True
            entry.set_translated_text(entry.original_text.strip())
            translated_entries.append(entry)

    def _handle_semantic_chunk_failure(
        self,
        planned: PlannedChunk,
        semantic_unit_by_index: dict[int, SemanticUnit],
        translated_entries: list[SubtitleEntry],
        report: TranslationReport,
        exc: Exception,
        progress: ProgressEmitter | None,
    ) -> None:
        source_entries = [
            entry
            for semantic_entry in planned.entries
            for entry in semantic_unit_by_index[semantic_entry.index].entries
        ]
        report.mark_failed(planned.index, [entry.index for entry in source_entries], exc)
        if progress is not None:
            progress.emit(
                stage="processing_chunks",
                detail="chunk_failed",
                status="failed",
                label="语义片段失败",
                message=f"语义片段 {planned.index + 1} 处理失败：{exc}",
                chunk=chunk_payload(
                    planned.entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                    status="failed",
                    detail="chunk_failed",
                    issue_summary=str(exc),
                ),
            )
        if self.config.pipeline.fallback_on_chunk_error == "abort":
            logger.error("语义chunk失败且配置为中止: chunk=%s error=%s", planned.index + 1, exc)
            raise exc
        logger.warning("语义chunk失败后回退到原文: chunk=%s error=%s", planned.index + 1, exc)
        for entry in source_entries:
            entry.needs_retranslation = True
            entry.set_translated_text(entry.original_text.strip())
            translated_entries.append(entry)

    def _save_checkpoint(
        self,
        checkpoint: CheckpointStore,
        subtitle: Subtitle,
        report: TranslationReport,
        translated_entries: list[SubtitleEntry],
        removed_entry_indices: set[int],
    ) -> None:
        report.removed_entry_indices = sorted(removed_entry_indices)
        checkpoint.save(
            self._checkpoint_subtitle(subtitle, translated_entries, removed_entry_indices),
            report,
        )

    def _checkpoint_subtitle(
        self,
        subtitle: Subtitle,
        translated_entries: list[SubtitleEntry],
        removed_entry_indices: set[int],
    ) -> Subtitle:
        removed_indices = removed_entry_indices or set()
        translated_by_index = {
            entry.index: entry
            for entry in translated_entries
            if entry.index not in removed_indices
        }
        checkpoint_entries: list[SubtitleEntry] = []
        seen_indices: set[int] = set()
        for entry in subtitle.entries:
            if entry.index in removed_indices:
                continue
            checkpoint_entry = translated_by_index.get(entry.index, entry)
            checkpoint_entries.append(checkpoint_entry)
            seen_indices.add(checkpoint_entry.index)

        for entry in translated_entries:
            if entry.index in removed_indices or entry.index in seen_indices:
                continue
            checkpoint_entries.append(entry)

        return Subtitle(sorted(checkpoint_entries, key=lambda item: item.index))

    def _restore_checkpoint(
        self,
        request: TranslationRequest,
        subtitle: Subtitle,
        checkpoint: CheckpointStore,
        report: TranslationReport,
    ) -> tuple[set[int], set[int]]:
        if not request.resume:
            return set(), set()

        data = checkpoint.load()
        entries = data.get("entries", {})
        report_data = data.get("report", {})
        removed_entry_indices = {
            int(index)
            for index in report_data.get("removed_entry_indices", [])
        }
        failed_entry_indices = {
            int(index)
            for failed in report_data.get("failed_chunks", [])
            for index in failed.get("entry_indices", [])
        }
        resumed_indices: set[int] = set()
        restored_entries: list[SubtitleEntry] = []
        for entry in subtitle.entries:
            if entry.index in removed_entry_indices:
                continue
            if entry.index in failed_entry_indices:
                restored_entries.append(entry)
                continue
            saved = entries.get(str(entry.index))
            if not saved:
                restored_entries.append(entry)
                continue
            restored_entry = SubtitleEntry.from_dict(saved)
            restored_entries.append(restored_entry)
            if restored_entry.translated_text.strip():
                resumed_indices.add(restored_entry.index)
        subtitle.entries = restored_entries
        report.resumed_entries = len(resumed_indices)
        report.removed_entry_indices = sorted(removed_entry_indices)
        report.auto_layout_repairs = [
            AutoLayoutRepair(**item)
            for item in report_data.get("auto_layout_repairs", [])
        ]
        report.final_output_entries = int(report_data.get("final_output_entries", 0) or 0)
        return resumed_indices, removed_entry_indices

    def _use_semantic_translation(self, _review_mode: str, units: list[SemanticUnit]) -> bool:
        mode = self.config.pipeline.semantic_translation
        if mode == "off":
            return False
        if mode == "always":
            return bool(units)
        return any(len(unit.entries) > 1 for unit in units)

    def _refine_translation_enabled(self, request: TranslationRequest) -> bool:
        if request.refine_translation is not None:
            return request.refine_translation
        return self.config.pipeline.refine_translation

    def _finalize_subtitle(
        self,
        subtitle: Subtitle,
        translated_entries: list[SubtitleEntry],
        removed_entry_indices: set[int] | None = None,
    ) -> None:
        removed_indices = removed_entry_indices or set()
        translated_by_index = {
            entry.index: entry
            for entry in translated_entries
            if entry.index not in removed_indices
        }
        for entry in subtitle.entries:
            if entry.index in removed_indices:
                continue
            if entry.index in translated_by_index:
                continue
            if not entry.translated_text.strip():
                entry.set_translated_text(entry.original_text.strip())
            translated_by_index[entry.index] = entry

        subtitle.entries = sorted(translated_by_index.values(), key=lambda item: item.index)
        subtitle.reorder_entries()

    def _review_port(self, review_mode: str) -> ReviewPort:
        if self.review_port:
            return self.review_port
        if review_mode == "tui":
            return TuiReviewPort()
        return AutoReviewPort()

    def _resolve_input(self, input_file: str, source_language: str, progress: ProgressEmitter) -> ResolvedInput:
        if not self._is_url(input_file):
            progress.emit(
                stage="prepare_input",
                detail="local_input",
                label="准备输入",
                message=f"使用本地输入：{input_file}",
            )
            return ResolvedInput(subtitle_file=input_file)

        from subtitle_llm.media import download, transcribe

        progress.emit(
            stage="prepare_input",
            detail="url_input",
            label="准备输入",
            message="检测到视频 URL，准备获取字幕或音频",
        )
        logger.info("检测到URL输入，准备下载或复用媒体: url=%s source_language=%s", input_file, source_language)
        output_dir = Path.cwd() / "data" / "input"
        result = download(input_file, output_dir, source_language, progress=progress)
        if len(result) == 3:
            _video_path, subtitle_path, audio_path = result
        else:
            _video_path, subtitle_path = result
            audio_path = None

        if subtitle_path:
            progress.emit(
                stage="prepare_input",
                detail="subtitle_ready",
                status="done",
                label="字幕就绪",
                message=f"已获取字幕：{subtitle_path}",
            )
            logger.info("URL输入解析到字幕: subtitle=%s video=%s", subtitle_path, _video_path)
            return ResolvedInput(subtitle_file=subtitle_path, video_file=_video_path)
        if not audio_path:
            raise RuntimeError("未找到字幕且无法提取音频")

        srt_path = output_dir / f"{Path(audio_path).stem}.srt"
        logger.info("URL输入未找到字幕，准备ASR转写: audio=%s output=%s", audio_path, srt_path)
        transcribed_path = transcribe(audio_path, source_language, srt_path, self.config.asr, progress=progress)
        progress.emit(
            stage="prepare_input",
            detail="asr_ready",
            status="done",
            label="ASR 完成",
            message=f"已生成字幕：{transcribed_path}",
        )
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


def translator_progress(translator: ChunkTranslator) -> ProgressEmitter:
    return translator.progress or ProgressEmitter("translate")


def semantic_layout_repair_reason(entry: SubtitleEntry) -> str | None:
    translated = entry.translated_text.strip()
    if is_orphan_punctuation(translated):
        return "punctuation_or_quote_tail"
    return None


def join_non_empty_text(*values: str) -> str:
    return " ".join(value.strip() for value in values if value.strip())


def join_translated_text(left: str, right: str, target_language: str) -> str:
    left = left.strip()
    right = right.strip()
    if not left:
        return right
    if not right:
        return left
    if is_orphan_punctuation(right) or is_cjk_language(target_language):
        return f"{left}{right}"
    return f"{left} {right}"


def is_cjk_language(language: str) -> bool:
    normalized = language.strip().lower()
    return (
        normalized in {"zh", "zh-cn", "zh_cn", "ja", "jp", "ko"}
        or "chinese" in normalized
        or "中文" in normalized
        or "japanese" in normalized
        or "korean" in normalized
    )


def chunk_quality_message(chunk_index: int, total_chunks: int, diagnosis) -> str:
    if diagnosis.has_issues:
        return (
            f"片段 {chunk_index + 1}/{total_chunks} 质量检查发现 "
            f"{diagnosis.flagged_entries} 行疑似问题：{diagnosis.summary}"
        )
    return f"片段 {chunk_index + 1}/{total_chunks} 质量检查通过"


def quality_chunk_status(diagnosis, review_port: ReviewPort) -> str:
    if not diagnosis.has_issues:
        return "done"
    if isinstance(review_port, AutoReviewPort):
        return "repairing"
    return "review"
