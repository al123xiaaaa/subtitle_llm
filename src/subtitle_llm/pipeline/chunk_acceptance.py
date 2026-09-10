"""翻译片段接受管线：片段从「译完」到「终态」的全部编排。

质量诊断 → 自动修复重译（劣化回滚）→ 语义布局修复 → 源文修正闸门 → TUI 复核
全部收敛在这一个 module 内；片段生命周期事件（ADR-0006）在此接线，
TranslationService 只保留任务级编排。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.llm.types import CompletionUsage
from subtitle_llm.pipeline.chunk_translator import ChunkTranslationResult, ChunkTranslator
from subtitle_llm.pipeline.chunks import PlannedChunk
from subtitle_llm.pipeline.lifecycle import (
    TranslationChunkLifecycle,
    TranslationChunkLifecycleEvent,
)
from subtitle_llm.pipeline.lifecycle.machines import CHUNK_TRANSITIONS
from subtitle_llm.pipeline.lifecycle.projectors import ChunkLifecycleProjector
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
    make_unit,
)
from subtitle_llm.pipeline.source_corrections import (
    SourceCorrectionFlag,
    find_unadopted_hard_corrections,
    source_corrections_from_context,
)
from subtitle_llm.pipeline.text import parse_translation_results
from subtitle_llm.progress_contract import ProgressContract
from subtitle_llm.progress_events import ProgressEmitter
from subtitle_llm.review import ReviewPort, ReviewResult
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


class ChunkAcceptance:
    """片段接受的深 module：窄 interface（accept / accept_semantic / handle_failure），
    内部承载诊断、修复、复核的全部策略。"""

    def __init__(
        self,
        *,
        config: AppConfig,
        translator: ChunkTranslator,
        quality_gate: QualityGate,
        review_port: ReviewPort,
        context: str,
        target_language: str,
        report: TranslationReport,
        run_ledger: RunLedger,
        refine_translation: bool = False,
    ):
        self.config = config
        self.translator = translator
        self.quality_gate = quality_gate
        self.review_port = review_port
        self.context = context
        self.target_language = target_language
        self.report = report
        self.run_ledger = run_ledger
        self.refine_translation = refine_translation
        self._progress = translator_progress_contract(translator)
        self._chunk_lifecycles: dict[int, TranslationChunkLifecycle] | None = None
        self._chunk_projector: ChunkLifecycleProjector | None = None

    def bind_lifecycle(
        self,
        chunk_lifecycles: dict[int, TranslationChunkLifecycle],
        chunk_projector: ChunkLifecycleProjector,
    ) -> None:
        """由协调器在片段入队后绑定，之后接受过程中的关键决策点会驱动状态机。"""
        self._chunk_lifecycles = chunk_lifecycles
        self._chunk_projector = chunk_projector

    def _apply_chunk_event(
        self,
        planned: PlannedChunk,
        event: TranslationChunkLifecycleEvent,
        *,
        semantic: bool = False,
        entries: list[SubtitleEntry] | None = None,
    ) -> None:
        if self._chunk_lifecycles is None or self._chunk_projector is None:
            return
        lifecycle = self._chunk_lifecycles[planned.index]
        # 复核/修复可能多轮循环：同一状态下重复的事件不再转移，停留在当前状态。
        if event is not TranslationChunkLifecycleEvent.FAILED and (lifecycle.state, event) not in CHUNK_TRANSITIONS:
            return
        lifecycle = lifecycle.apply(event)
        self._chunk_lifecycles[planned.index] = lifecycle
        self._chunk_projector.project(
            lifecycle.state,
            entries or planned.entries,
            chunk_index=planned.index,
            total_chunks=self.report.total_chunks,
            semantic=semantic,
            emit_progress=False,
        )

    # ------------------------------------------------------------------
    # 公开 interface
    # ------------------------------------------------------------------

    def translate_semantic(
        self,
        planned: PlannedChunk,
        semantic_unit_by_index: dict[int, SemanticUnit],
    ) -> ChunkTranslationResult:
        if self.config.pipeline.semantic_output_granularity == "cue":
            return self.translator.translate_semantic_timed_cues(
                self._semantic_units_for_planned(planned, semantic_unit_by_index),
                self.context,
                self.target_language,
                planned.boundary_context,
                planned.index,
            )
        return self.translator.translate_semantic_and_refine(
            planned.entries,
            self.context,
            self.target_language,
            planned.boundary_context,
            planned.index,
            refine_translation=self.refine_translation,
        )

    def accept(
        self,
        planned: PlannedChunk,
        result: ChunkTranslationResult,
        translated_entries: list[SubtitleEntry],
    ) -> None:
        report = self.report
        report.token_usage.add_usage(result.usage.to_dict())
        translation = result.translation
        for entry, refined_text in parse_translation_results(translation, planned.entries):
            entry.set_translated_text(refined_text.strip())

        diagnosis = self.quality_gate.diagnose_chunk(
            planned.entries,
            translation=translation,
            target_language=self.target_language,
        )
        if self.translator.trace_recorder:
            self.translator.trace_recorder.update_quality(result.final_trace_id, diagnosis)
        self.quality_gate.apply_diagnosis(planned.entries, diagnosis)
        review_policy = ReviewPolicy.from_review_port(self.review_port)
        self._progress.quality_checked(
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
                self._apply_chunk_event(planned, TranslationChunkLifecycleEvent.REPAIR_REQUIRED)
                before_repair = snapshot_entry_translations(planned.entries)
                repair_usage = CompletionUsage()
                repaired_result = self.translator.repair_translation_traced(
                    planned.entries,
                    translation,
                    self.target_language,
                    usage=repair_usage,
                    quality_report=diagnosis.to_prompt_report(),
                    chunk_index=planned.index,
                )
                repaired = repaired_result.text
                report.token_usage.add_usage(repair_usage.to_dict())
                for entry, refined_text in parse_translation_results(repaired, planned.entries):
                    entry.needs_retranslation = False
                    entry.set_translated_text(refined_text.strip())
                repaired_diagnosis = self.quality_gate.diagnose_chunk(
                    planned.entries,
                    translation=repaired,
                    target_language=self.target_language,
                )
                diagnosis = self._settle_auto_repair(
                    planned,
                    planned.entries,
                    diagnosis=diagnosis,
                    repaired_diagnosis=repaired_diagnosis,
                    before_repair=before_repair,
                    trace_ids=[repaired_result.trace_id],
                    review_policy=review_policy,
                    label="chunk",
                )
                self._apply_chunk_event(planned, TranslationChunkLifecycleEvent.REPAIR_COMPLETED)
                logger.info(
                    "chunk自动重译完成: chunk=%s reliability=%s flagged=%s",
                    planned.index + 1,
                    repaired_diagnosis.reliability,
                    repaired_diagnosis.flagged_entries,
                )
            else:
                self._apply_chunk_event(planned, TranslationChunkLifecycleEvent.REVIEW_REQUIRED)
                self._review_chunk_with_tui(planned)

        planned.entries = self._apply_source_correction_gate(planned, planned.entries)
        if review_policy.uses_manual_review and any(entry.needs_retranslation for entry in planned.entries):
            self._apply_chunk_event(planned, TranslationChunkLifecycleEvent.REVIEW_REQUIRED)
            self._review_chunk_with_tui(planned)

        for entry in planned.entries:
            translated_entries.append(entry)
        self._progress.chunk_accepted(
            planned.entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            warning=any(entry.needs_retranslation for entry in planned.entries),
        )
        logger.info("chunk接受完成: chunk=%s entries=%s", planned.index + 1, len(planned.entries))

    def accept_semantic(
        self,
        planned: PlannedChunk,
        result: ChunkTranslationResult,
        semantic_unit_by_index: dict[int, SemanticUnit],
        translated_entries: list[SubtitleEntry],
    ) -> None:
        if self.config.pipeline.semantic_output_granularity == "cue":
            self._accept_semantic_timed_chunk(
                planned,
                result,
                self._semantic_units_for_planned(planned, semantic_unit_by_index),
                translated_entries,
            )
            return
        self._accept_semantic_chunk(planned, result, semantic_unit_by_index, translated_entries)

    def handle_failure(
        self,
        planned: PlannedChunk,
        exc: Exception,
        translated_entries: list[SubtitleEntry],
    ) -> None:
        self.report.mark_failed(planned.index, [entry.index for entry in planned.entries], exc)
        if self.translator.progress is not None:
            self._progress.chunk_failed(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=self.report.total_chunks,
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

    def handle_semantic_failure(
        self,
        planned: PlannedChunk,
        semantic_unit_by_index: dict[int, SemanticUnit],
        exc: Exception,
        translated_entries: list[SubtitleEntry],
    ) -> None:
        source_entries = [
            entry
            for semantic_entry in planned.entries
            for entry in semantic_unit_by_index[semantic_entry.index].entries
        ]
        self.report.mark_failed(planned.index, [entry.index for entry in source_entries], exc)
        if self.translator.progress is not None:
            self._progress.chunk_failed(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=self.report.total_chunks,
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

    # ------------------------------------------------------------------
    # 语义 cue 粒度接受
    # ------------------------------------------------------------------

    def _accept_semantic_timed_chunk(
        self,
        planned: PlannedChunk,
        result: ChunkTranslationResult,
        semantic_units: list[SemanticUnit],
        translated_entries: list[SubtitleEntry],
    ) -> None:
        report = self.report
        report.token_usage.add_usage(result.usage.to_dict())
        source_entries = result.chunk
        diagnosis = self.quality_gate.diagnose_chunk(
            source_entries,
            translation=result.translation,
            target_language=self.target_language,
        )
        if self.translator.trace_recorder:
            self.translator.trace_recorder.update_quality(result.final_trace_id, diagnosis)
        self.quality_gate.apply_diagnosis(source_entries, diagnosis)
        review_policy = ReviewPolicy.from_review_port(self.review_port)
        self._progress.quality_checked(
            source_entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            diagnosis=diagnosis,
            auto_repair=review_policy.quality_visual_auto_repair(diagnosis),
        )

        if diagnosis.has_issues and review_policy.should_auto_repair(diagnosis):
            self._apply_chunk_event(planned, TranslationChunkLifecycleEvent.REPAIR_REQUIRED, semantic=True)
            before_repair = snapshot_entry_translations(source_entries)
            selected_entries = [entry for entry in source_entries if entry.needs_retranslation]
            if not selected_entries:
                selected_entries = source_entries
            outcome = self._run_semantic_repair_batches(
                planned,
                selected_entries,
                semantic_units,
                source_entries,
                diagnosis,
                intent="auto_quality",
                selected_indices={entry.index for entry in selected_entries},
                stage="semantic-repair",
            )
            repaired_diagnosis = self.quality_gate.diagnose_chunk(source_entries, target_language=self.target_language)
            diagnosis = self._settle_auto_repair(
                planned,
                source_entries,
                diagnosis=diagnosis,
                repaired_diagnosis=repaired_diagnosis,
                before_repair=before_repair,
                trace_ids=outcome.trace_ids,
                review_policy=review_policy,
                label="语义cue",
            )
            self._apply_chunk_event(planned, TranslationChunkLifecycleEvent.REPAIR_COMPLETED, semantic=True)

            source_entries = repair_semantic_layout(
                planned,
                source_entries,
                semantic_units,
                self.run_ledger,
                report,
                self.target_language,
                allow_auto_merge=False,
            )
        source_entries = self._apply_source_correction_gate(planned, source_entries)
        layout_review_indices = {
            entry.index
            for entry in source_entries
            if entry.needs_retranslation
        }
        source_diagnosis = self.quality_gate.diagnose_chunk(source_entries, target_language=self.target_language)
        self.quality_gate.apply_diagnosis(source_entries, source_diagnosis)
        for entry in source_entries:
            if entry.index in layout_review_indices:
                entry.needs_retranslation = True
        diagnosis = source_diagnosis

        if review_policy.should_manual_review(source_diagnosis) or (
            review_policy.uses_manual_review and layout_review_indices
        ):
            logger.warning(
                "语义cue质量诊断命中，进入TUI复核: chunk=%s reliability=%s flagged=%s layout_review=%s summary=%s",
                planned.index + 1,
                source_diagnosis.reliability,
                source_diagnosis.flagged_entries,
                sorted(layout_review_indices),
                source_diagnosis.summary if source_diagnosis.has_issues else "semantic layout diagnosis marked entries for review.",
            )
            self._apply_chunk_event(planned, TranslationChunkLifecycleEvent.REVIEW_REQUIRED, semantic=True)
            source_entries = self._review_semantic_chunk_with_tui(
                planned,
                source_entries,
                semantic_units,
                allow_layout_auto_merge=False,
            )
            diagnosis = self.quality_gate.diagnose_chunk(source_entries, target_language=self.target_language)

        translated_entries.extend(source_entries)
        self._progress.chunk_accepted(
            source_entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            semantic=True,
            warning=bool(diagnosis.has_issues or any(entry.needs_retranslation for entry in source_entries)),
        )
        logger.info("语义cue chunk接受完成: chunk=%s entries=%s", planned.index + 1, len(source_entries))

    # ------------------------------------------------------------------
    # 语义单元粒度接受
    # ------------------------------------------------------------------

    def _accept_semantic_chunk(
        self,
        planned: PlannedChunk,
        result: ChunkTranslationResult,
        semantic_unit_by_index: dict[int, SemanticUnit],
        translated_entries: list[SubtitleEntry],
    ) -> None:
        report = self.report
        report.token_usage.add_usage(result.usage.to_dict())
        translation = result.translation
        for entry, refined_text in parse_translation_results(translation, planned.entries):
            entry.set_translated_text(refined_text.strip())

        diagnosis = self.quality_gate.diagnose_chunk(
            planned.entries,
            translation=translation,
            target_language=self.target_language,
        )
        if self.translator.trace_recorder:
            self.translator.trace_recorder.update_quality(result.final_trace_id, diagnosis)
        self.quality_gate.apply_diagnosis(planned.entries, diagnosis)
        review_policy = ReviewPolicy.from_review_port(self.review_port)
        self._progress.quality_checked(
            planned.entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            diagnosis=diagnosis,
            auto_repair=review_policy.quality_visual_auto_repair(diagnosis),
        )
        if diagnosis.has_issues and review_policy.should_auto_repair(diagnosis):
            self._apply_chunk_event(planned, TranslationChunkLifecycleEvent.REPAIR_REQUIRED, semantic=True)
            before_repair = snapshot_entry_translations(planned.entries)
            repair_usage = CompletionUsage()
            repaired_result = self.translator.repair_translation_traced(
                planned.entries,
                translation,
                self.target_language,
                usage=repair_usage,
                quality_report=diagnosis.to_prompt_report(),
                chunk_index=planned.index,
            )
            repaired = repaired_result.text
            report.token_usage.add_usage(repair_usage.to_dict())
            for entry, refined_text in parse_translation_results(repaired, planned.entries):
                entry.needs_retranslation = False
                entry.set_translated_text(refined_text.strip())
            repaired_diagnosis = self.quality_gate.diagnose_chunk(
                planned.entries,
                translation=repaired,
                target_language=self.target_language,
            )
            diagnosis = self._settle_auto_repair(
                planned,
                planned.entries,
                diagnosis=diagnosis,
                repaired_diagnosis=repaired_diagnosis,
                before_repair=before_repair,
                trace_ids=[repaired_result.trace_id],
                review_policy=review_policy,
                label="语义chunk",
            )
            self._apply_chunk_event(planned, TranslationChunkLifecycleEvent.REPAIR_COMPLETED, semantic=True)

        source_entries: list[SubtitleEntry] = []
        semantic_units = [semantic_unit_by_index[semantic_entry.index] for semantic_entry in planned.entries]
        for semantic_entry, unit in zip(planned.entries, semantic_units, strict=True):
            source_entries.extend(apply_semantic_translation(
                unit,
                semantic_entry.translated_text,
                target_language=self.target_language,
            ))

        source_entries = repair_semantic_layout(
            planned,
            source_entries,
            semantic_units,
            self.run_ledger,
            report,
            self.target_language,
        )
        source_entries = self._apply_source_correction_gate(planned, source_entries)
        layout_review_indices = {
            entry.index
            for entry in source_entries
            if entry.needs_retranslation
        }
        source_diagnosis = self.quality_gate.diagnose_chunk(source_entries, target_language=self.target_language)
        self.quality_gate.apply_diagnosis(source_entries, source_diagnosis)
        for entry in source_entries:
            if entry.index in layout_review_indices:
                entry.needs_retranslation = True
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
            self._apply_chunk_event(planned, TranslationChunkLifecycleEvent.REVIEW_REQUIRED, semantic=True)
            source_entries = self._review_semantic_chunk_with_tui(
                planned,
                source_entries,
                semantic_units,
            )

        translated_entries.extend(source_entries)

        self._progress.chunk_accepted(
            planned.entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            semantic=True,
            warning=bool(diagnosis.has_issues or any(entry.needs_retranslation for entry in source_entries)),
        )
        logger.info("语义chunk接受完成: chunk=%s units=%s", planned.index + 1, len(planned.entries))

    # ------------------------------------------------------------------
    # 自动修复的采纳 / 回滚（三条接受路径共用）
    # ------------------------------------------------------------------

    def _settle_auto_repair(
        self,
        planned: PlannedChunk,
        entries: list[SubtitleEntry],
        *,
        diagnosis: ChunkDiagnosis,
        repaired_diagnosis: ChunkDiagnosis,
        before_repair: EntryTranslationSnapshot,
        trace_ids: list[str | None],
        review_policy: ReviewPolicy,
        label: str,
    ) -> ChunkDiagnosis:
        """对比修复前后诊断：劣化则回滚到初译，否则采纳修复结果。返回生效的诊断。"""
        repair_downgraded = repair_diagnosis_is_downgrade(diagnosis, repaired_diagnosis)
        if self.translator.trace_recorder:
            for trace_id in trace_ids:
                self.translator.trace_recorder.update_quality(
                    trace_id,
                    repaired_diagnosis,
                    status="failed" if repair_downgraded else None,
                )
        if repair_downgraded:
            restore_entry_translations(before_repair)
            self.quality_gate.apply_diagnosis(entries, diagnosis)
            logger.warning(
                "%s自动修复劣化，已回滚到初译: chunk=%s before=%s/%s after=%s/%s",
                label,
                planned.index + 1,
                diagnosis.reliability,
                diagnosis.flagged_entries,
                repaired_diagnosis.reliability,
                repaired_diagnosis.flagged_entries,
            )
        else:
            self.quality_gate.apply_diagnosis(entries, repaired_diagnosis)
            diagnosis = repaired_diagnosis
        self._progress.quality_checked(
            entries,
            chunk_index=planned.index,
            total_chunks=self.report.total_chunks,
            diagnosis=repaired_diagnosis,
            auto_repair=False,
            stage_status=review_policy.post_repair_stage_status(repaired_diagnosis),
            chunk_status=review_policy.post_repair_chunk_status(repaired_diagnosis),
        )
        return diagnosis

    # ------------------------------------------------------------------
    # TUI 复核
    # ------------------------------------------------------------------

    def _review_chunk_with_tui(self, planned: PlannedChunk) -> None:
        report = self.report
        max_rounds = 2
        for review_round in range(1, max_rounds + 1):
            self._progress.tui_wait(
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
            )
            review_result = self.review_port.review(
                planned.entries,
                planned.index,
                report.total_chunks,
                completed_chunks=report.completed_chunks,
            )
            self.run_ledger.record_removed_indices(review_result.removed_entry_indices)
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

            outcome = self._apply_tui_review_result(planned, review_result)
            if not outcome.did_attempt:
                for entry in planned.entries:
                    entry.needs_retranslation = False
                self._progress.tui_accept(
                    planned.entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                )
                logger.info("TUI审核接受当前chunk: chunk=%s round=%s", planned.index + 1, review_round)
                return

            diagnosis = self.quality_gate.diagnose_chunk(planned.entries, target_language=self.target_language)
            if self.translator.trace_recorder:
                for trace_id in outcome.trace_ids:
                    self.translator.trace_recorder.update_quality(trace_id, diagnosis)
            self.quality_gate.apply_diagnosis(planned.entries, diagnosis)
            if not diagnosis.has_issues:
                self._progress.tui_quality_passed(
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

        if self.translator.trace_recorder:
            for trace_id in outcome.trace_ids:
                self.translator.trace_recorder.update_quality(trace_id, diagnosis, status="failed")
        self._progress.tui_warning(
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
    ) -> TuiReviewOutcome:
        report = self.report
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
            self._progress.ordinary_tui_retranslation_started(
                ordinary_entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
            )
            selected_result = self.translator.translate_and_refine(
                ordinary_entries,
                self.context,
                self.target_language,
                planned.boundary_context,
                planned.index,
                "tui-ordinary",
                refine_translation=self.refine_translation,
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
            drift_position = entry_position(planned.entries, drift_start)
            if drift_position is None:
                logger.warning("TUI漂移起点不存在，跳过漂移重译: chunk=%s drift_start=%s", planned.index + 1, drift_start)
                return outcome

            stable_anchors = planned.entries[max(0, drift_position - 6):drift_position]
            drift_entries = planned.entries[drift_position:]
            drift_usage = CompletionUsage()
            self._progress.alignment_drift_started(
                drift_entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                drift_start=drift_start,
            )
            drift_result = self.translator.retranslate_alignment_drift_traced(
                drift_entries,
                stable_anchors,
                self.context,
                self.target_language,
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

    def _review_semantic_chunk_with_tui(
        self,
        planned: PlannedChunk,
        source_entries: list[SubtitleEntry],
        semantic_units: list[SemanticUnit],
        allow_layout_auto_merge: bool = True,
    ) -> list[SubtitleEntry]:
        report = self.report
        max_rounds = 2
        cue_to_unit = {
            entry.index: unit
            for unit in semantic_units
            for entry in unit.entries
        }
        current_entries = source_entries
        outcome = TuiReviewOutcome(retranslated=False)
        diagnosis = self.quality_gate.diagnose_chunk(current_entries, target_language=self.target_language)

        for review_round in range(1, max_rounds + 1):
            self._progress.tui_wait(
                current_entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                semantic=True,
            )
            review_result = self.review_port.review(
                current_entries,
                planned.index,
                report.total_chunks,
                completed_chunks=report.completed_chunks,
            )
            self.run_ledger.record_removed_indices(review_result.removed_entry_indices)
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

            review_diagnosis = self.quality_gate.diagnose_chunk(current_entries, target_language=self.target_language)
            outcome = self._apply_semantic_tui_review_result(
                planned,
                current_entries,
                semantic_units,
                cue_to_unit,
                review_result,
                review_diagnosis,
            )
            if not outcome.did_attempt:
                for entry in current_entries:
                    entry.needs_retranslation = False
                self._progress.tui_accept(
                    current_entries,
                    chunk_index=planned.index,
                    total_chunks=report.total_chunks,
                    semantic=True,
                )
                return current_entries

            current_entries = repair_semantic_layout(
                planned,
                current_entries,
                semantic_units,
                self.run_ledger,
                report,
                self.target_language,
                allow_auto_merge=allow_layout_auto_merge,
            )
            layout_review_indices = {
                entry.index
                for entry in current_entries
                if entry.needs_retranslation
            }
            diagnosis = self.quality_gate.diagnose_chunk(current_entries, target_language=self.target_language)
            if self.translator.trace_recorder:
                for trace_id in outcome.trace_ids:
                    self.translator.trace_recorder.update_quality(trace_id, diagnosis)
            self.quality_gate.apply_diagnosis(current_entries, diagnosis)
            for entry in current_entries:
                if entry.index in layout_review_indices:
                    entry.needs_retranslation = True
            if not diagnosis.has_issues and not layout_review_indices:
                self._progress.tui_quality_passed(
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

        if self.translator.trace_recorder:
            for trace_id in outcome.trace_ids:
                self.translator.trace_recorder.update_quality(trace_id, diagnosis, status="suspicious")
        self._progress.tui_warning(
            current_entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            issue_summary=diagnosis.summary,
            semantic=True,
        )
        return current_entries

    def _apply_semantic_tui_review_result(
        self,
        planned: PlannedChunk,
        current_entries: list[SubtitleEntry],
        semantic_units: list[SemanticUnit],
        cue_to_unit: dict[int, SemanticUnit],
        review_result: ReviewResult,
        diagnosis: ChunkDiagnosis,
    ) -> TuiReviewOutcome:
        if review_result.alignment_drift_start_index is not None:
            return self._apply_semantic_alignment_drift_review_result(
                planned,
                current_entries,
                semantic_units,
                review_result.alignment_drift_start_index,
                diagnosis,
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

        self._progress.semantic_tui_retranslation_started(
            current_entries,
            chunk_index=planned.index,
            total_chunks=self.report.total_chunks,
            semantic_unit_count=len(units_to_translate),
        )
        selected_indices = {entry.index for entry in review_result.entries_to_retranslate}
        outcome = self._run_semantic_repair_batches(
            planned,
            output_entries,
            units_to_translate,
            current_entries,
            diagnosis,
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

    def _apply_semantic_alignment_drift_review_result(
        self,
        planned: PlannedChunk,
        current_entries: list[SubtitleEntry],
        semantic_units: list[SemanticUnit],
        drift_start: int,
        diagnosis: ChunkDiagnosis,
    ) -> TuiReviewOutcome:
        outcome = TuiReviewOutcome(retranslated=False)
        drift_position = entry_position(current_entries, drift_start)
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
        self._progress.alignment_drift_started(
            drift_entries,
            chunk_index=planned.index,
            total_chunks=self.report.total_chunks,
            drift_start=drift_start,
            semantic=True,
        )
        outcome = self._run_semantic_repair_batches(
            planned,
            drift_entries,
            drift_units,
            current_entries,
            diagnosis,
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

    # ------------------------------------------------------------------
    # 语义修复重译批处理（带自动拆分重试）
    # ------------------------------------------------------------------

    def _run_semantic_repair_batches(
        self,
        planned: PlannedChunk,
        output_entries: list[SubtitleEntry],
        semantic_units: list[SemanticUnit],
        current_entries: list[SubtitleEntry],
        diagnosis: ChunkDiagnosis,
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
            repair_result = self.translator.repair_semantic_timed_cues_traced(
                output_entries,
                repair_brief.to_prompt_text(),
                self.context,
                self.target_language,
                planned.boundary_context,
                repair_usage,
                chunk_index=planned.index,
                stage=stage,
            )
        except Exception as exc:
            self.report.token_usage.add_usage(repair_usage.to_dict())
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

        self.report.token_usage.add_usage(repair_usage.to_dict())
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

    # ------------------------------------------------------------------
    # 源文修正闸门
    # ------------------------------------------------------------------

    def _apply_source_correction_gate(
        self,
        planned: PlannedChunk,
        entries: list[SubtitleEntry],
    ) -> list[SubtitleEntry]:
        corrections = source_corrections_from_context(self.context)
        if not corrections:
            return entries

        flags = find_unadopted_hard_corrections(corrections, entries)
        if not flags:
            return entries

        logger.warning(
            "source_correction_gate命中: chunk=%s flags=%s",
            planned.index + 1,
            [
                {
                    "cue_id": flag.cue_id,
                    "observed": flag.observed,
                    "corrected": flag.corrected,
                    "type": flag.correction_type,
                }
                for flag in flags
            ],
        )
        entry_by_index = {entry.index: entry for entry in entries}
        for flag in flags:
            entry = entry_by_index.get(flag.cue_id)
            if entry is None:
                continue
            self._repair_source_correction_flag(planned, entry, flag, entries)

        remaining_flags = find_unadopted_hard_corrections(corrections, entries)
        for flag in remaining_flags:
            if entry := entry_by_index.get(flag.cue_id):
                entry.needs_retranslation = True
        if remaining_flags:
            logger.warning(
                "source_correction_gate仍有未采纳专名修正: chunk=%s flags=%s",
                planned.index + 1,
                [
                    {
                        "cue_id": flag.cue_id,
                        "observed": flag.observed,
                        "corrected": flag.corrected,
                    }
                    for flag in remaining_flags
                ],
            )
        return entries

    def _repair_source_correction_flag(
        self,
        planned: PlannedChunk,
        entry: SubtitleEntry,
        flag: SourceCorrectionFlag,
        entries: list[SubtitleEntry],
    ) -> None:
        before_repair = snapshot_entry_translations([entry])
        repair_usage = CompletionUsage()
        try:
            repaired = self.translator.repair_source_correction_traced(
                entry,
                flag,
                nearby_entries(entries, entry.index, window=1),
                self.target_language,
                repair_usage,
                chunk_index=planned.index,
            )
        except Exception as exc:
            self.report.token_usage.add_usage(repair_usage.to_dict())
            restore_entry_translations(before_repair)
            entry.needs_retranslation = True
            logger.warning(
                "source_correction_gate单cue修复失败，保留当前译文: chunk=%s cue=%s observed=%r corrected=%r error=%s",
                planned.index + 1,
                entry.index,
                flag.observed,
                flag.corrected,
                exc,
            )
            return

        self.report.token_usage.add_usage(repair_usage.to_dict())
        entry.set_translated_text(repaired.text.strip())
        entry.needs_retranslation = False
        logger.info(
            "source_correction_gate单cue修复完成: chunk=%s cue=%s observed=%r corrected=%r",
            planned.index + 1,
            entry.index,
            flag.observed,
            flag.corrected,
        )

    # ------------------------------------------------------------------
    # 语义单元辅助
    # ------------------------------------------------------------------

    def _semantic_units_for_planned(
        self,
        planned: PlannedChunk,
        semantic_unit_by_index: dict[int, SemanticUnit],
    ) -> list[SemanticUnit]:
        return [semantic_unit_by_index[semantic_entry.index] for semantic_entry in planned.entries]

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


# ----------------------------------------------------------------------
# module 级 helper
# ----------------------------------------------------------------------


def translator_progress_contract(translator: ChunkTranslator) -> ProgressContract:
    return ProgressContract(translator.progress or ProgressEmitter("translate"))


def repair_semantic_layout(
    planned: PlannedChunk,
    source_entries: list[SubtitleEntry],
    semantic_units: list[SemanticUnit],
    run_ledger: RunLedger,
    report: TranslationReport,
    target_language: str,
    *,
    allow_auto_merge: bool = True,
) -> list[SubtitleEntry]:
    """语义布局修复：不改写译文本身，只调整译文在时间轴字幕上的分布。"""
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
                    if allow_auto_merge:
                        auto_merge_at = position
                    else:
                        left.needs_retranslation = True
                        entry.needs_retranslation = True
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


def entry_position(entries: list[SubtitleEntry], entry_index: int) -> int | None:
    for position, entry in enumerate(entries):
        if entry.index == entry_index:
            return position
    return None


def nearby_entries(
    entries: list[SubtitleEntry],
    entry_index: int,
    *,
    window: int,
) -> list[SubtitleEntry]:
    position = entry_position(entries, entry_index)
    if position is None:
        return []
    start = max(0, position - window)
    end = min(len(entries), position + window + 1)
    return entries[start:end]


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
