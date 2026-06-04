from __future__ import annotations

import concurrent.futures
import logging
from dataclasses import dataclass
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
from subtitle_llm.pipeline.quality import QualityGate
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.text import parse_translation_results
from subtitle_llm.review import AutoReviewPort, ReviewPort, TuiReviewPort
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


@dataclass
class TranslationResult:
    subtitle: Subtitle
    report: TranslationReport


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

    def translate(self, request: TranslationRequest) -> TranslationResult:
        logger.info(
            "翻译任务开始: input=%s target_language=%s source_language=%s resume=%s review_mode=%s",
            request.input_file,
            request.target_language,
            request.source_language,
            request.resume,
            request.review_mode or self.config.pipeline.review_mode,
        )
        resolved_input = self._resolve_input(request.input_file, request.source_language)
        input_file = resolved_input.subtitle_file
        output_file = request.output_file or self._default_output_file(
            input_file,
            request.target_language,
            request.source_language,
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
        logger.info(
            "翻译文件已准备: resolved_input=%s output=%s checkpoint=%s context=%s",
            input_file,
            output_file,
            checkpoint_file,
            context_file,
        )

        subtitle = SubtitleIO.read(
            input_file,
            max_chars=self.config.pipeline.max_chars,
            max_duration=self.config.pipeline.max_duration,
        )
        report.total_entries = len(subtitle.entries)
        logger.info("字幕读取完成: entries=%s", report.total_entries)

        checkpoint = CheckpointStore(
            checkpoint_file=checkpoint_file,
            input_fingerprint=file_fingerprint(input_file),
            target_language=request.target_language,
            output_format=output_format,
            config_version=self.config.config_version,
        )
        resumed_indices = self._restore_checkpoint(request, subtitle, checkpoint, report)
        if resumed_indices:
            logger.info("断点恢复完成: resumed_entries=%s", len(resumed_indices))

        context_service = ContextService(
            self.summary_client,
            self.config.summary_model,
            review_enabled=self.config.pipeline.context_review,
        )
        context_source = "\n".join(
            [entry.original_text for entry in subtitle.entries if len(entry.original_text) >= 10]
        )
        context, context_usage = context_service.build_context(context_source, request.target_language)
        report.token_usage.add_usage(context_usage.to_dict())
        context_service.save_context(context, context_file)
        logger.info(
            "上下文生成完成: context_file=%s context_tokens=%s",
            context_file,
            context_usage.total_tokens,
        )

        planner = ChunkPlanner(
            chunk_size=self.config.pipeline.chunk_size,
            context_window_size=self.config.pipeline.context_window_size,
            ignore_subtitle_length=self.config.pipeline.ignore_subtitle_length,
        )
        planned_chunks = planner.plan(subtitle.entries, resumed_indices=resumed_indices)
        report.total_chunks = len(planned_chunks)
        report.short_entries = len(
            [
                entry
                for entry in subtitle.entries
                if len(entry.original_text.strip()) <= self.config.pipeline.ignore_subtitle_length
                and entry.index not in resumed_indices
            ]
        )
        boundary_risks_by_key: dict[tuple[int, int], dict] = {}
        for planned in planned_chunks:
            for risk in planned.boundary_risks:
                boundary_risks_by_key[(risk["before_index"], risk["after_index"])] = risk
        report.boundary_risks = list(boundary_risks_by_key.values())
        report.boundary_risk_count = len(report.boundary_risks)
        logger.info(
            "chunk规划完成: chunks=%s short_entries=%s boundary_risks=%s",
            report.total_chunks,
            report.short_entries,
            report.boundary_risk_count,
        )

        translator = ChunkTranslator(self.translation_client, self.config.translation_model)
        quality_gate = QualityGate()
        review_mode = request.review_mode or self.config.pipeline.review_mode
        review_port = self._review_port(review_mode)
        logger.info("审核模式: %s", review_mode)
        translated_entries: list[SubtitleEntry] = [
            entry for entry in subtitle.entries if entry.index in resumed_indices
        ]

        try:
            self._run_chunks(
                planned_chunks,
                translator,
                quality_gate,
                review_port,
                context,
                request.target_language,
                subtitle,
                translated_entries,
                checkpoint,
                report,
            )

            self._finalize_subtitle(subtitle, translated_entries)
            report.stage = "完成"
            report.processed_entries = len(subtitle.entries)
            checkpoint.save(subtitle, report)
            SubtitleIO.write_srt(subtitle, output_file, output_format=output_format)
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
        checkpoint: CheckpointStore,
        report: TranslationReport,
    ) -> None:
        done_futures: set[concurrent.futures.Future] = set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.pipeline.threads) as executor:
            future_to_chunk = {
                executor.submit(
                    translator.translate_and_refine,
                    planned.entries,
                    context,
                    target_language,
                    planned.boundary_context,
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
                            target_language,
                            translated_entries,
                            report,
                        )
                    except Exception as exc:
                        logger.exception(
                            "chunk处理失败: chunk=%s entries=%s",
                            planned.index + 1,
                            [entry.index for entry in planned.entries],
                        )
                        self._handle_chunk_failure(planned, translated_entries, report, exc)
                    finally:
                        report.completed_chunks += 1
                        report.processed_entries = len({entry.index for entry in translated_entries})
                        checkpoint.save(subtitle, report)

    def _accept_chunk(
        self,
        planned: PlannedChunk,
        result: ChunkTranslationResult,
        translator: ChunkTranslator,
        quality_gate: QualityGate,
        review_port: ReviewPort,
        target_language: str,
        translated_entries: list[SubtitleEntry],
        report: TranslationReport,
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
        quality_gate.apply_diagnosis(planned.entries, diagnosis)
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
                repaired = translator.repair_translation(
                    planned.entries,
                    translation,
                    target_language,
                    usage=repair_usage,
                    quality_report=diagnosis.to_prompt_report(),
                )
                report.token_usage.add_usage(repair_usage.to_dict())
                for entry, refined_text in parse_translation_results(repaired, planned.entries):
                    entry.needs_retranslation = False
                    entry.set_translated_text(refined_text.strip())
                repaired_diagnosis = quality_gate.diagnose_chunk(
                    planned.entries,
                    translation=repaired,
                    target_language=target_language,
                )
                quality_gate.apply_diagnosis(planned.entries, repaired_diagnosis)
                logger.info(
                    "chunk自动重译完成: chunk=%s reliability=%s flagged=%s",
                    planned.index + 1,
                    repaired_diagnosis.reliability,
                    repaired_diagnosis.flagged_entries,
                )
            else:
                review_result = review_port.review(planned.entries, planned.index, report.total_chunks)
                planned.entries = review_result.chunk
                logger.info(
                    "TUI审核完成: chunk=%s selected_for_retranslation=%s",
                    planned.index + 1,
                    len(review_result.entries_to_retranslate),
                )
                if review_result.entries_to_retranslate:
                    selected = review_result.entries_to_retranslate
                    selected_result = translator.translate_and_refine(
                        selected,
                        "",
                        target_language,
                        planned.boundary_context,
                    )
                    report.token_usage.add_usage(selected_result.usage.to_dict())
                    for entry, refined_text in parse_translation_results(selected_result.translation, selected_result.chunk):
                        entry.needs_retranslation = False
                        entry.set_translated_text(refined_text.strip())
                    logger.info("TUI选中重译完成: chunk=%s selected=%s", planned.index + 1, len(selected))

        for entry in planned.entries:
            translated_entries.append(entry)
        logger.info("chunk接受完成: chunk=%s entries=%s", planned.index + 1, len(planned.entries))

    def _handle_chunk_failure(
        self,
        planned: PlannedChunk,
        translated_entries: list[SubtitleEntry],
        report: TranslationReport,
        exc: Exception,
    ) -> None:
        report.mark_failed(planned.index, [entry.index for entry in planned.entries], exc)
        if self.config.pipeline.fallback_on_chunk_error == "abort":
            logger.error("chunk失败且配置为中止: chunk=%s error=%s", planned.index + 1, exc)
            raise exc
        logger.warning("chunk失败后回退到原文: chunk=%s error=%s", planned.index + 1, exc)
        for entry in planned.entries:
            entry.needs_retranslation = True
            entry.set_translated_text(entry.original_text.strip())
            translated_entries.append(entry)

    def _restore_checkpoint(
        self,
        request: TranslationRequest,
        subtitle: Subtitle,
        checkpoint: CheckpointStore,
        report: TranslationReport,
    ) -> set[int]:
        if not request.resume:
            return set()

        data = checkpoint.load()
        entries = data.get("entries", {})
        failed_entry_indices = {
            int(index)
            for failed in data.get("report", {}).get("failed_chunks", [])
            for index in failed.get("entry_indices", [])
        }
        resumed_indices: set[int] = set()
        for entry in subtitle.entries:
            if entry.index in failed_entry_indices:
                continue
            saved = entries.get(str(entry.index))
            if not saved:
                continue
            entry.set_translated_text(saved.get("translated_text", ""))
            entry.needs_retranslation = saved.get("needs_retranslation", False)
            if entry.translated_text.strip():
                resumed_indices.add(entry.index)
        report.resumed_entries = len(resumed_indices)
        return resumed_indices

    def _finalize_subtitle(self, subtitle: Subtitle, translated_entries: list[SubtitleEntry]) -> None:
        translated_by_index = {entry.index: entry for entry in translated_entries}
        for entry in subtitle.entries:
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

    def _resolve_input(self, input_file: str, source_language: str) -> ResolvedInput:
        if not self._is_url(input_file):
            return ResolvedInput(subtitle_file=input_file)

        from subtitle_llm.media import download, transcribe

        logger.info("检测到URL输入，准备下载或复用媒体: url=%s source_language=%s", input_file, source_language)
        output_dir = Path.cwd() / "data" / "input"
        result = download(input_file, output_dir, source_language)
        if len(result) == 3:
            _video_path, subtitle_path, audio_path = result
        else:
            _video_path, subtitle_path = result
            audio_path = None

        if subtitle_path:
            logger.info("URL输入解析到字幕: subtitle=%s video=%s", subtitle_path, _video_path)
            return ResolvedInput(subtitle_file=subtitle_path, video_file=_video_path)
        if not audio_path:
            raise RuntimeError("未找到字幕且无法提取音频")

        srt_path = output_dir / f"{Path(audio_path).stem}.srt"
        logger.info("URL输入未找到字幕，准备ASR转写: audio=%s output=%s", audio_path, srt_path)
        transcribed_path = transcribe(audio_path, source_language, srt_path, self.config.asr)
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
