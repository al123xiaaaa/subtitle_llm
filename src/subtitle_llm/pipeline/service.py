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
from subtitle_llm.pipeline.quality import ChunkDiagnosis, QualityGate
from subtitle_llm.pipeline.repair_brief import RepairBrief
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.review_policy import ReviewPolicy
from subtitle_llm.pipeline.run_ledger import RunLedger
from subtitle_llm.pipeline.semantic_layout import (
    diagnose_layout_pair,
    is_orphan_punctuation,
    latin_token_split,
    starts_with_closing_pair,
)
from subtitle_llm.pipeline.semantic_units import (
    SemanticUnit,
    apply_semantic_translation,
    build_semantic_units,
    make_unit,
    semantic_entries,
)
from subtitle_llm.pipeline.text import parse_translation_results
from subtitle_llm.progress_contract import ProgressContract
from subtitle_llm.progress_events import ProgressEmitter
from subtitle_llm.review import AutoReviewPort, ReviewPort, ReviewResult, TuiReviewPort
from subtitle_llm.settings import AppConfig

logger = logging.getLogger(__name__)

SEMANTIC_REPAIR_BATCH_SIZE = 8
RELIABILITY_RANK = {"high": 0, "medium": 1, "low": 2, "very_low": 3}
STRUCTURAL_REPAIR_ISSUES = {
    "index_missing",
    "index_extra",
    "index_order_error",
    "missing_translation",
    "placeholder_translation",
}


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
    attempted: bool = False

    @property
    def did_attempt(self) -> bool:
        return self.retranslated or self.attempted

    def merge(self, other: "TuiReviewOutcome") -> None:
        self.retranslated = self.retranslated or other.retranslated
        self.attempted = self.attempted or other.attempted
        self.trace_ids.extend(other.trace_ids)


EntryTranslationSnapshot = list[tuple[SubtitleEntry, str, bool]]


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
        progress_contract = ProgressContract(progress)
        progress_contract.task_prepared()
        logger.info(
            "翻译任务开始: input=%s target_language=%s source_language=%s resume=%s review_mode=%s refine_translation=%s",
            request.input_file,
            request.target_language,
            request.source_language,
            request.resume,
            request.review_mode or self.config.pipeline.review_mode,
            self._refine_translation_enabled(request),
        )
        resolved_input = self._resolve_input(request.input_file, request.source_language, progress_contract)
        input_file = resolved_input.subtitle_file
        output_file = request.output_file or self._default_output_file(
            input_file,
            request.target_language,
            request.source_language,
        )
        progress_contract.output_resolved(output_file)
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
        progress_contract.diagnostics_prepared(trace_recorder.trace_dir)
        logger.info("LLM诊断目录已准备: %s", trace_recorder.trace_dir)
        logger.info(
            "翻译文件已准备: resolved_input=%s output=%s checkpoint=%s context=%s",
            input_file,
            output_file,
            checkpoint_file,
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

        checkpoint = CheckpointStore(
            checkpoint_file=checkpoint_file,
            input_fingerprint=file_fingerprint(checkpoint_input_file),
            target_language=request.target_language,
            output_format=output_format,
            config_version=self.config.config_version,
        )
        checkpoint_restore = RunLedger.restore_checkpoint(
            resume=request.resume,
            subtitle=subtitle,
            checkpoint=checkpoint,
            report=report,
        )
        resumed_indices = checkpoint_restore.resumed_indices
        run_ledger = checkpoint_restore.ledger
        progress_contract.checkpoint_restored(len(resumed_indices))
        if resumed_indices:
            logger.info("断点恢复完成: resumed_entries=%s", len(resumed_indices))

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
        progress_contract.chunks_planned(total_chunks=report.total_chunks, short_entries=report.short_entries)
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
        progress_contract.chunk_pool_started(total_chunks=report.total_chunks, threads=self.config.pipeline.threads)
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
                    run_ledger,
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
                    run_ledger,
                    checkpoint,
                    report,
                    refine_translation,
                )

            progress_contract.finalizing_subtitle()
            run_ledger.finalize_subtitle(subtitle, translated_entries, report)
            run_ledger.save_checkpoint(checkpoint, subtitle, report, translated_entries)
            progress_contract.writing_srt(output_file)
            SubtitleIO.write_srt(subtitle, output_file, output_format=output_format)
            progress_contract.srt_written(output_file)
            if emit_complete:
                progress_contract.complete(total_chunks=report.total_chunks)
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
        run_ledger: RunLedger,
        checkpoint: CheckpointStore,
        report: TranslationReport,
        refine_translation: bool,
    ) -> None:
        done_futures: set[concurrent.futures.Future] = set()
        for planned in planned_chunks:
            translator_progress_contract(translator).chunk_queued(
                planned.entries,
                chunk_index=planned.index,
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
                            run_ledger,
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
                        report.processed_entries = run_ledger.processed_entry_count(translated_entries)
                        run_ledger.save_checkpoint(checkpoint, subtitle, report, translated_entries)
                        translator_progress_contract(translator).checkpoint_saved(
                            planned.entries,
                            chunk_index=planned.index,
                            total_chunks=report.total_chunks,
                            warning=bool(planned.entries and any(entry.needs_retranslation for entry in planned.entries)),
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
        run_ledger: RunLedger,
        checkpoint: CheckpointStore,
        report: TranslationReport,
        refine_translation: bool,
    ) -> None:
        done_futures: set[concurrent.futures.Future] = set()
        for planned in planned_chunks:
            translator_progress_contract(translator).chunk_queued(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                semantic=True,
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
                            run_ledger,
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
                        report.processed_entries = run_ledger.processed_entry_count(translated_entries)
                        run_ledger.save_checkpoint(checkpoint, subtitle, report, translated_entries)
                        translator_progress_contract(translator).checkpoint_saved(
                            planned.entries,
                            chunk_index=planned.index,
                            total_chunks=report.total_chunks,
                            semantic=True,
                            warning=bool(
                                planned.entries and any(entry.needs_retranslation for entry in planned.entries)
                            ),
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
        run_ledger: RunLedger,
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
        review_policy = ReviewPolicy.from_review_port(review_port)
        translator_progress_contract(translator).quality_checked(
            planned.entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            diagnosis=diagnosis,
            auto_repair=review_policy.quality_visual_auto_repair(diagnosis),
        )
        if diagnosis.has_issues and review_policy.should_auto_repair(diagnosis):
            before_repair = snapshot_entry_translations(planned.entries)
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
            repair_downgraded = repair_diagnosis_is_downgrade(diagnosis, repaired_diagnosis)
            if translator.trace_recorder:
                translator.trace_recorder.update_quality(
                    repaired_result.trace_id,
                    repaired_diagnosis,
                    status="failed" if repair_downgraded else None,
                )
            if repair_downgraded:
                restore_entry_translations(before_repair)
                quality_gate.apply_diagnosis(planned.entries, diagnosis)
                logger.warning(
                    "语义chunk自动修复劣化，已回滚到初译: chunk=%s before=%s/%s after=%s/%s",
                    planned.index + 1,
                    diagnosis.reliability,
                    diagnosis.flagged_entries,
                    repaired_diagnosis.reliability,
                    repaired_diagnosis.flagged_entries,
                )
            else:
                quality_gate.apply_diagnosis(planned.entries, repaired_diagnosis)
                diagnosis = repaired_diagnosis
            translator_progress_contract(translator).quality_checked(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                diagnosis=repaired_diagnosis,
                auto_repair=False,
                stage_status="warning" if repaired_diagnosis.has_issues else "done",
                chunk_status="warning" if repaired_diagnosis.has_issues else "done",
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
            run_ledger,
            report,
            target_language,
        )
        layout_review_indices = {
            entry.index
            for entry in source_entries
            if entry.needs_retranslation
        }
        source_diagnosis = quality_gate.diagnose_chunk(source_entries, target_language=target_language)
        quality_gate.apply_diagnosis(source_entries, source_diagnosis)
        for entry in source_entries:
            if entry.index in layout_review_indices:
                entry.needs_retranslation = True
        review_policy = ReviewPolicy.from_review_port(review_port)
        if review_policy.should_manual_review(source_diagnosis) or (
            review_policy.uses_manual_review and layout_review_indices
        ):
            logger.warning(
                "语义chunk映射后质量诊断命中，进入TUI复核: chunk=%s reliability=%s flagged=%s layout_review=%s summary=%s",
                planned.index + 1,
                source_diagnosis.reliability,
                source_diagnosis.flagged_entries,
                sorted(layout_review_indices),
                source_diagnosis.summary if source_diagnosis.has_issues else "semantic layout diagnosis marked entries for review.",
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
                run_ledger,
                report,
                refine_translation,
            )

        translated_entries.extend(source_entries)

        translator_progress_contract(translator).chunk_accepted(
            planned.entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            semantic=True,
            warning=bool(diagnosis.has_issues or any(entry.needs_retranslation for entry in source_entries)),
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
        run_ledger: RunLedger,
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
            translator_progress_contract(translator).tui_wait(
                current_entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                semantic=True,
            )
            review_result = review_port.review(
                current_entries,
                planned.index,
                report.total_chunks,
                completed_chunks=report.completed_chunks,
            )
            run_ledger.record_removed_indices(review_result.removed_entry_indices)
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

            review_diagnosis = quality_gate.diagnose_chunk(current_entries, target_language=target_language)
            outcome = self._apply_semantic_tui_review_result(
                planned,
                current_entries,
                semantic_units,
                cue_to_unit,
                review_result,
                review_diagnosis,
                translator,
                context,
                target_language,
                report,
                refine_translation,
            )
            if not outcome.did_attempt:
                for entry in current_entries:
                    entry.needs_retranslation = False
                translator_progress_contract(translator).tui_accept(
                    current_entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                    semantic=True,
                )
                return current_entries

            current_entries = self._repair_semantic_layout(
                planned,
                current_entries,
                semantic_units,
                run_ledger,
                report,
                target_language,
            )
            layout_review_indices = {
                entry.index
                for entry in current_entries
                if entry.needs_retranslation
            }
            diagnosis = quality_gate.diagnose_chunk(current_entries, target_language=target_language)
            if translator.trace_recorder:
                for trace_id in outcome.trace_ids:
                    translator.trace_recorder.update_quality(trace_id, diagnosis)
            quality_gate.apply_diagnosis(current_entries, diagnosis)
            for entry in current_entries:
                if entry.index in layout_review_indices:
                    entry.needs_retranslation = True
            if not diagnosis.has_issues and not layout_review_indices:
                translator_progress_contract(translator).tui_quality_passed(
                    current_entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                    semantic=True,
                )
                return current_entries

            logger.warning(
                "语义TUI重译后质量诊断仍命中: chunk=%s round=%s reliability=%s flagged=%s summary=%s",
                planned.index + 1,
                review_round,
                diagnosis.reliability,
                diagnosis.flagged_entries + len(layout_review_indices),
                diagnosis.summary if diagnosis.has_issues else "semantic layout diagnosis marked entries for review.",
            )

        if translator.trace_recorder:
            for trace_id in outcome.trace_ids:
                translator.trace_recorder.update_quality(trace_id, diagnosis, status="suspicious")
        translator_progress_contract(translator).tui_warning(
            current_entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            issue_summary=diagnosis.summary,
            semantic=True,
        )
        return current_entries

    def _repair_semantic_layout(
        self,
        planned: PlannedChunk,
        source_entries: list[SubtitleEntry],
        semantic_units: list[SemanticUnit],
        run_ledger: RunLedger,
        report: TranslationReport,
        target_language: str,
    ) -> list[SubtitleEntry]:
        source_by_index = {entry.index: entry for entry in source_entries}
        removed_in_chunk: set[int] = set()

        for unit in semantic_units:
            unit_entries = [
                entry
                for unit_entry in unit.entries
                if (entry := source_by_index.get(unit_entry.index)) is not None
                and entry.index not in removed_in_chunk
            ]
            if not unit_entries:
                continue

            while len(unit_entries) > 1:
                auto_merge_at: int | None = None
                for position, entry in enumerate(unit_entries):
                    left = unit_entries[position - 1] if position > 0 else None
                    issue = diagnose_layout_pair(left, entry, target_language=target_language)
                    if issue is None:
                        continue
                    logger.info(
                        "semantic_layout_issue: 语义布局诊断 chunk=%s semantic_unit=%s left_index=%s right_index=%s issue=%s action=%s reason=%s",
                        planned.index + 1,
                        unit.index,
                        issue.left_index,
                        issue.right_index,
                        issue.issue_type,
                        issue.action,
                        issue.reason,
                    )
                    if issue.action == "auto_merge" and left is not None:
                        auto_merge_at = position
                        break
                    if issue.action == "review" and left is not None:
                        left.needs_retranslation = True
                        entry.needs_retranslation = True
                    if issue.action == "quality":
                        entry.needs_retranslation = True

                if auto_merge_at is None:
                    break

                left = unit_entries[auto_merge_at - 1]
                right = unit_entries[auto_merge_at]
                issue = diagnose_layout_pair(left, right, target_language=target_language)
                reason = issue.issue_type if issue else "semantic_layout"
                before_left = left.translated_text
                before_right = right.translated_text
                left.end_time = right.end_time
                left.original_text = join_non_empty_text(left.original_text, right.original_text)
                left.translated_text = join_translated_text(
                    left.translated_text,
                    right.translated_text,
                    target_language,
                )
                left.needs_retranslation = left.needs_retranslation or right.needs_retranslation
                removed_in_chunk.add(right.index)
                source_by_index.pop(right.index, None)
                unit_entries.pop(auto_merge_at)
                repair = run_ledger.record_auto_layout_repair(
                    report,
                    merged_index=left.index,
                    removed_index=right.index,
                    reason=reason,
                )
                logger.info(
                    "semantic_layout_auto_merge: 语义布局自动合并 chunk=%s semantic_unit=%s merged_index=%s removed_index=%s reason=%s before_left=%r before_right=%r after=%r",
                    planned.index + 1,
                    unit.index,
                    repair.merged_index,
                    repair.removed_index,
                    repair.reason,
                    before_left,
                    before_right,
                    left.translated_text,
                )

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
        diagnosis: ChunkDiagnosis,
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
                semantic_units,
                review_result.alignment_drift_start_index,
                diagnosis,
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

        output_entries = self._semantic_repair_output_entries(
            units_to_translate,
            current_entries,
            review_result,
        )
        if not output_entries:
            return outcome

        translator_progress_contract(translator).semantic_tui_retranslation_started(
            current_entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            semantic_unit_count=len(units_to_translate),
        )
        selected_indices = {entry.index for entry in review_result.entries_to_retranslate}
        outcome = self._run_semantic_repair_batches(
            planned,
            output_entries,
            units_to_translate,
            current_entries,
            diagnosis,
            translator,
            context,
            target_language,
            report,
            intent="retranslation_range",
            selected_indices=selected_indices,
            stage="tui-semantic-repair",
        )
        logger.info(
            "TUI语义修复重译完成: chunk=%s semantic_context_units=%s output_indices=%s selected_indices=%s cascade_start=%s",
            planned.index + 1,
            len(units_to_translate),
            [entry.index for entry in output_entries],
            sorted(selected_indices),
            review_result.cascade_start_index,
        )
        return outcome

    def _run_semantic_repair_batches(
        self,
        planned: PlannedChunk,
        output_entries: list[SubtitleEntry],
        semantic_units: list[SemanticUnit],
        current_entries: list[SubtitleEntry],
        diagnosis: ChunkDiagnosis,
        translator: ChunkTranslator,
        context: str,
        target_language: str,
        report: TranslationReport,
        *,
        intent: str,
        selected_indices: set[int],
        stable_anchors: list[SubtitleEntry] | None = None,
        stage: str,
    ) -> TuiReviewOutcome:
        outcome = TuiReviewOutcome(retranslated=False, attempted=True)
        for batch in split_entry_batches(output_entries, SEMANTIC_REPAIR_BATCH_SIZE):
            batch_outcome = self._run_semantic_repair_batch_with_fallback(
                planned,
                batch,
                semantic_units,
                current_entries,
                diagnosis,
                translator,
                context,
                target_language,
                report,
                intent=intent,
                selected_indices=selected_indices,
                stable_anchors=stable_anchors or [],
                stage=stage,
            )
            outcome.merge(batch_outcome)
        return outcome

    def _run_semantic_repair_batch_with_fallback(
        self,
        planned: PlannedChunk,
        output_entries: list[SubtitleEntry],
        semantic_units: list[SemanticUnit],
        current_entries: list[SubtitleEntry],
        diagnosis: ChunkDiagnosis,
        translator: ChunkTranslator,
        context: str,
        target_language: str,
        report: TranslationReport,
        *,
        intent: str,
        selected_indices: set[int],
        stable_anchors: list[SubtitleEntry],
        stage: str,
    ) -> TuiReviewOutcome:
        outcome = self._run_semantic_repair_batch_once(
            planned,
            output_entries,
            semantic_units,
            current_entries,
            diagnosis,
            translator,
            context,
            target_language,
            report,
            intent=intent,
            selected_indices=selected_indices,
            stable_anchors=stable_anchors,
            stage=stage,
        )
        if outcome.retranslated or len(output_entries) <= 1:
            return outcome

        logger.warning(
            "TUI语义修复重译批次失败，自动拆分重试: chunk=%s stage=%s output_indices=%s",
            planned.index + 1,
            stage,
            [entry.index for entry in output_entries],
        )
        split_outcome = TuiReviewOutcome(retranslated=False, attempted=True)
        for smaller_batch in split_entry_batches(output_entries, max(1, len(output_entries) // 2)):
            split_outcome.merge(
                self._run_semantic_repair_batch_with_fallback(
                    planned,
                    smaller_batch,
                    semantic_units,
                    current_entries,
                    diagnosis,
                    translator,
                    context,
                    target_language,
                    report,
                    intent=intent,
                    selected_indices=selected_indices,
                    stable_anchors=stable_anchors,
                    stage=stage,
                )
            )
        return split_outcome

    def _run_semantic_repair_batch_once(
        self,
        planned: PlannedChunk,
        output_entries: list[SubtitleEntry],
        semantic_units: list[SemanticUnit],
        current_entries: list[SubtitleEntry],
        diagnosis: ChunkDiagnosis,
        translator: ChunkTranslator,
        context: str,
        target_language: str,
        report: TranslationReport,
        *,
        intent: str,
        selected_indices: set[int],
        stable_anchors: list[SubtitleEntry],
        stage: str,
    ) -> TuiReviewOutcome:
        repair_brief = RepairBrief(
            intent=intent,
            output_entries=output_entries,
            semantic_units=semantic_context_for_output_entries(semantic_units, output_entries),
            current_entries=current_entries,
            diagnosis=diagnosis,
            selected_indices=selected_indices,
            stable_anchors=stable_anchors,
        )
        repair_usage = CompletionUsage()
        try:
            repair_result = translator.repair_semantic_timed_cues_traced(
                output_entries,
                repair_brief.to_prompt_text(),
                context,
                target_language,
                planned.boundary_context,
                repair_usage,
                chunk_index=planned.index,
                stage=stage,
            )
        except Exception as exc:
            report.token_usage.add_usage(repair_usage.to_dict())
            for entry in output_entries:
                entry.needs_retranslation = True
            logger.warning(
                "TUI语义修复重译批次失败，保留当前译文: chunk=%s stage=%s output_indices=%s error=%s",
                planned.index + 1,
                stage,
                [entry.index for entry in output_entries],
                exc,
            )
            return TuiReviewOutcome(retranslated=False, attempted=True)

        report.token_usage.add_usage(repair_usage.to_dict())
        for entry, refined_text in parse_translation_results(repair_result.text, output_entries):
            entry.needs_retranslation = False
            entry.set_translated_text(refined_text.strip())

        outcome = TuiReviewOutcome(retranslated=True, attempted=True)
        if repair_result.trace_id:
            outcome.trace_ids.append(repair_result.trace_id)
        logger.info(
            "TUI语义修复重译批次完成: chunk=%s stage=%s output_indices=%s semantic_context_units=%s",
            planned.index + 1,
            stage,
            [entry.index for entry in output_entries],
            len(repair_brief.semantic_units),
        )
        return outcome

    def _semantic_repair_output_entries(
        self,
        units_to_translate: list[SemanticUnit],
        current_entries: list[SubtitleEntry],
        review_result: ReviewResult,
    ) -> list[SubtitleEntry]:
        cascade_start = review_result.cascade_start_index
        if cascade_start is not None:
            candidate_indices = {
                entry.index
                for unit in units_to_translate
                for entry in unit.entries
                if entry.index >= cascade_start
            }
        else:
            selected_indices = {entry.index for entry in review_result.entries_to_retranslate}
            candidate_indices = selected_indices or {
                entry.index
                for unit in units_to_translate
                for entry in unit.entries
            }
        return [entry for entry in current_entries if entry.index in candidate_indices]

    def _apply_semantic_alignment_drift_review_result(
        self,
        planned: PlannedChunk,
        current_entries: list[SubtitleEntry],
        semantic_units: list[SemanticUnit],
        drift_start: int,
        diagnosis: ChunkDiagnosis,
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
        current_by_index = {entry.index: entry for entry in current_entries}
        drift_units = [
            unit
            for unit in (
                self._current_semantic_unit(unit, current_by_index)
                for unit in semantic_units
                if unit.entries and unit.entries[-1].index >= drift_start
            )
            if unit is not None
        ]
        translator_progress_contract(translator).alignment_drift_started(
            drift_entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            drift_start=drift_start,
            semantic=True,
        )
        outcome = self._run_semantic_repair_batches(
            planned,
            drift_entries,
            drift_units,
            current_entries,
            diagnosis,
            translator,
            context,
            target_language,
            report,
            intent="alignment_drift",
            selected_indices={entry.index for entry in drift_entries},
            stable_anchors=stable_anchors,
            stage="tui-semantic-drift-repair",
        )

        logger.info(
            "语义TUI对齐漂移修复重译完成: chunk=%s drift_start=%s entries=%s anchors=%s write_back_indices=%s",
            planned.index + 1,
            drift_start,
            len(drift_entries),
            len(stable_anchors),
            [entry.index for entry in drift_entries],
        )
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
        run_ledger: RunLedger,
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
        review_policy = ReviewPolicy.from_review_port(review_port)
        translator_progress_contract(translator).quality_checked(
            planned.entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            diagnosis=diagnosis,
            auto_repair=review_policy.quality_visual_auto_repair(diagnosis),
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
            if review_policy.should_auto_repair(diagnosis):
                before_repair = snapshot_entry_translations(planned.entries)
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
                repair_downgraded = repair_diagnosis_is_downgrade(diagnosis, repaired_diagnosis)
                if translator.trace_recorder:
                    translator.trace_recorder.update_quality(
                        repaired_result.trace_id,
                        repaired_diagnosis,
                        status="failed" if repair_downgraded else None,
                    )
                if repair_downgraded:
                    restore_entry_translations(before_repair)
                    quality_gate.apply_diagnosis(planned.entries, diagnosis)
                    logger.warning(
                        "chunk自动修复劣化，已回滚到初译: chunk=%s before=%s/%s after=%s/%s",
                        planned.index + 1,
                        diagnosis.reliability,
                        diagnosis.flagged_entries,
                        repaired_diagnosis.reliability,
                        repaired_diagnosis.flagged_entries,
                    )
                else:
                    quality_gate.apply_diagnosis(planned.entries, repaired_diagnosis)
                    diagnosis = repaired_diagnosis
                translator_progress_contract(translator).quality_checked(
                    planned.entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                    diagnosis=repaired_diagnosis,
                    auto_repair=False,
                    stage_status=review_policy.post_repair_stage_status(repaired_diagnosis),
                    chunk_status=review_policy.post_repair_chunk_status(repaired_diagnosis),
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
                    run_ledger,
                    report,
                    refine_translation,
                )

        for entry in planned.entries:
            translated_entries.append(entry)
        translator_progress_contract(translator).chunk_accepted(
            planned.entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            warning=any(entry.needs_retranslation for entry in planned.entries),
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
        run_ledger: RunLedger,
        report: TranslationReport,
        refine_translation: bool,
    ) -> None:
        max_rounds = 2
        for review_round in range(1, max_rounds + 1):
            translator_progress_contract(translator).tui_wait(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
            )
            review_result = review_port.review(
                planned.entries,
                planned.index,
                report.total_chunks,
                completed_chunks=report.completed_chunks,
            )
            run_ledger.record_removed_indices(review_result.removed_entry_indices)
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
            if not outcome.did_attempt:
                for entry in planned.entries:
                    entry.needs_retranslation = False
                translator_progress_contract(translator).tui_accept(
                    planned.entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                )
                logger.info("TUI审核接受当前chunk: chunk=%s round=%s", planned.index + 1, review_round)
                return

            diagnosis = quality_gate.diagnose_chunk(planned.entries, target_language=target_language)
            if translator.trace_recorder:
                for trace_id in outcome.trace_ids:
                    translator.trace_recorder.update_quality(trace_id, diagnosis)
            quality_gate.apply_diagnosis(planned.entries, diagnosis)
            if not diagnosis.has_issues:
                translator_progress_contract(translator).tui_quality_passed(
                    planned.entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
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
        translator_progress_contract(translator).tui_warning(
            planned.entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            issue_summary=diagnosis.summary,
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
            translator_progress_contract(translator).ordinary_tui_retranslation_started(
                ordinary_entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
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
            translator_progress_contract(translator).alignment_drift_started(
                drift_entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                drift_start=drift_start,
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
            ProgressContract(progress).chunk_failed(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                error=exc,
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
            ProgressContract(progress).chunk_failed(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                error=exc,
                semantic=True,
            )
        if self.config.pipeline.fallback_on_chunk_error == "abort":
            logger.error("语义chunk失败且配置为中止: chunk=%s error=%s", planned.index + 1, exc)
            raise exc
        logger.warning("语义chunk失败后回退到原文: chunk=%s error=%s", planned.index + 1, exc)
        for entry in source_entries:
            entry.needs_retranslation = True
            entry.set_translated_text(entry.original_text.strip())
            translated_entries.append(entry)

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

    def _review_port(self, review_mode: str) -> ReviewPort:
        if self.review_port:
            return self.review_port
        if review_mode == "tui":
            return TuiReviewPort()
        return AutoReviewPort()

    def _resolve_input(self, input_file: str, source_language: str, progress: ProgressContract) -> ResolvedInput:
        if not self._is_url(input_file):
            progress.local_input_selected(input_file)
            return ResolvedInput(subtitle_file=input_file)

        from subtitle_llm.media import download, transcribe

        progress.url_input_detected()
        logger.info("检测到URL输入，准备下载或复用媒体: url=%s source_language=%s", input_file, source_language)
        output_dir = Path.cwd() / "data" / "input"
        result = download(input_file, output_dir, source_language, progress=progress.emitter)
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


def translator_progress_contract(translator: ChunkTranslator) -> ProgressContract:
    return ProgressContract(translator.progress or ProgressEmitter("translate"))


def snapshot_entry_translations(entries: list[SubtitleEntry]) -> EntryTranslationSnapshot:
    return [(entry, entry.translated_text, entry.needs_retranslation) for entry in entries]


def restore_entry_translations(snapshot: EntryTranslationSnapshot) -> None:
    for entry, translated_text, needs_retranslation in snapshot:
        entry.translated_text = translated_text
        entry.needs_retranslation = needs_retranslation


def repair_diagnosis_is_downgrade(before: ChunkDiagnosis, after: ChunkDiagnosis) -> bool:
    before_rank = RELIABILITY_RANK.get(before.reliability, 1)
    after_rank = RELIABILITY_RANK.get(after.reliability, 1)
    if after_rank > before_rank:
        return True
    if after.flagged_entries > before.flagged_entries:
        return True

    before_issue_types = {issue.issue_type for issue in before.issues}
    after_issue_types = {issue.issue_type for issue in after.issues}
    return bool((after_issue_types - before_issue_types) & STRUCTURAL_REPAIR_ISSUES)


def join_non_empty_text(*values: str) -> str:
    return " ".join(value.strip() for value in values if value.strip())


def join_translated_text(left: str, right: str, target_language: str) -> str:
    del target_language
    left = left.strip()
    right = right.strip()
    if not left:
        return right
    if not right:
        return left
    if is_orphan_punctuation(right) or latin_token_split(left, right) or starts_with_closing_pair(right):
        return f"{left}{right}"
    return f"{left} {right}"


def split_entry_batches(entries: list[SubtitleEntry], batch_size: int) -> list[list[SubtitleEntry]]:
    if not entries:
        return []
    safe_batch_size = max(1, batch_size)
    return [
        entries[start:start + safe_batch_size]
        for start in range(0, len(entries), safe_batch_size)
    ]


def semantic_context_for_output_entries(
    semantic_units: list[SemanticUnit],
    output_entries: list[SubtitleEntry],
) -> list[SemanticUnit]:
    if not semantic_units or not output_entries:
        return semantic_units
    output_indices = {entry.index for entry in output_entries}
    matching_positions = [
        position
        for position, unit in enumerate(semantic_units)
        if any(entry.index in output_indices for entry in unit.entries)
    ]
    if not matching_positions:
        return semantic_units
    start = max(0, min(matching_positions) - 1)
    end = min(len(semantic_units), max(matching_positions) + 2)
    return semantic_units[start:end]
