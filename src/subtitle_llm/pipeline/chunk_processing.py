"""翻译片段并发协调器：只负责线程池、片段入队与生命周期骨架事件。

片段从「译完」到「终态」的全部策略（诊断、修复、复核）在
chunk_acceptance.ChunkAcceptance 里；这里不再持有任何编排决策。
"""

from __future__ import annotations

import concurrent.futures
import logging
from dataclasses import dataclass

from subtitle_llm.domain import Subtitle
from subtitle_llm.pipeline.chunk_acceptance import ChunkAcceptance, translator_progress_contract
from subtitle_llm.pipeline.chunk_translator import ChunkTranslator
from subtitle_llm.pipeline.chunks import PlannedChunk
from subtitle_llm.pipeline.lifecycle import (
    TranslationChunkLifecycle,
    TranslationChunkLifecycleEvent,
    TranslationChunkState,
)
from subtitle_llm.pipeline.lifecycle.projectors import ChunkLifecycleProjector
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.run_ledger import RunLedger
from subtitle_llm.pipeline.semantic_units import SemanticUnit
from subtitle_llm.pipeline.task_store import TranslationTaskStore
from subtitle_llm.progress_contract import ProgressContract

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkProcessingCoordinator:
    threads: int
    semantic_output_granularity: str

    def run_chunks(
        self,
        planned_chunks: list[PlannedChunk],
        translator: ChunkTranslator,
        acceptance: ChunkAcceptance,
        subtitle: Subtitle,
        translated_entries: list,
        run_ledger: RunLedger,
        task_store: TranslationTaskStore,
        task_id: str,
        report: TranslationReport,
    ) -> None:
        progress_contract = translator_progress_contract(translator)
        chunk_projector = ChunkLifecycleProjector(task_store, task_id, progress_contract)
        chunk_lifecycles = self._queue_chunks(
            planned_chunks,
            chunk_projector,
            total_chunks=report.total_chunks,
        )
        acceptance.bind_lifecycle(chunk_lifecycles, chunk_projector)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.threads) as executor:
            future_to_chunk: dict[concurrent.futures.Future, PlannedChunk] = {}
            for planned in planned_chunks:
                chunk_lifecycles[planned.index] = self._project_chunk_event(
                    chunk_lifecycles[planned.index],
                    TranslationChunkLifecycleEvent.TRANSLATION_STARTED,
                    chunk_projector,
                    planned,
                    report.total_chunks,
                )
                future_to_chunk[
                    executor.submit(
                        translator.translate_and_refine,
                        planned.entries,
                        acceptance.context,
                        acceptance.target_language,
                        planned.boundary_context,
                        planned.index,
                        refine_translation=acceptance.refine_translation,
                    )
                ] = planned
            self._consume_chunk_futures(
                future_to_chunk,
                chunk_lifecycles,
                chunk_projector,
                progress_contract,
                subtitle,
                translated_entries,
                run_ledger,
                task_store,
                task_id,
                report,
                accept_result=lambda planned, result: acceptance.accept(planned, result, translated_entries),
                handle_failure=lambda planned, exc: acceptance.handle_failure(planned, exc, translated_entries),
            )

    def run_semantic_chunks(
        self,
        planned_chunks: list[PlannedChunk],
        semantic_unit_by_index: dict[int, SemanticUnit],
        translator: ChunkTranslator,
        acceptance: ChunkAcceptance,
        subtitle: Subtitle,
        translated_entries: list,
        run_ledger: RunLedger,
        task_store: TranslationTaskStore,
        task_id: str,
        report: TranslationReport,
    ) -> None:
        progress_contract = translator_progress_contract(translator)
        chunk_projector = ChunkLifecycleProjector(task_store, task_id, progress_contract)
        chunk_lifecycles = self._queue_chunks(
            planned_chunks,
            chunk_projector,
            total_chunks=report.total_chunks,
            semantic=True,
        )
        acceptance.bind_lifecycle(chunk_lifecycles, chunk_projector)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.threads) as executor:
            future_to_chunk: dict[concurrent.futures.Future, PlannedChunk] = {}
            for planned in planned_chunks:
                chunk_lifecycles[planned.index] = self._project_chunk_event(
                    chunk_lifecycles[planned.index],
                    TranslationChunkLifecycleEvent.TRANSLATION_STARTED,
                    chunk_projector,
                    planned,
                    report.total_chunks,
                    semantic=True,
                )
                future_to_chunk[
                    executor.submit(
                        acceptance.translate_semantic,
                        planned,
                        semantic_unit_by_index,
                    )
                ] = planned
            self._consume_chunk_futures(
                future_to_chunk,
                chunk_lifecycles,
                chunk_projector,
                progress_contract,
                subtitle,
                translated_entries,
                run_ledger,
                task_store,
                task_id,
                report,
                semantic=True,
                accept_result=lambda planned, result: acceptance.accept_semantic(
                    planned,
                    result,
                    semantic_unit_by_index,
                    translated_entries,
                ),
                handle_failure=lambda planned, exc: acceptance.handle_semantic_failure(
                    planned,
                    semantic_unit_by_index,
                    exc,
                    translated_entries,
                ),
            )

    def _queue_chunks(
        self,
        planned_chunks: list[PlannedChunk],
        chunk_projector: ChunkLifecycleProjector,
        *,
        total_chunks: int,
        semantic: bool = False,
    ) -> dict[int, TranslationChunkLifecycle]:
        chunk_lifecycles: dict[int, TranslationChunkLifecycle] = {}
        for planned in planned_chunks:
            lifecycle = TranslationChunkLifecycle()
            chunk_lifecycles[planned.index] = lifecycle
            chunk_projector.project(
                lifecycle.state,
                planned.entries,
                chunk_index=planned.index,
                total_chunks=total_chunks,
                semantic=semantic,
            )
        return chunk_lifecycles

    def _consume_chunk_futures(
        self,
        future_to_chunk: dict[concurrent.futures.Future, PlannedChunk],
        chunk_lifecycles: dict[int, TranslationChunkLifecycle],
        chunk_projector: ChunkLifecycleProjector,
        progress_contract: ProgressContract,
        subtitle: Subtitle,
        translated_entries: list,
        run_ledger: RunLedger,
        task_store: TranslationTaskStore,
        task_id: str,
        report: TranslationReport,
        *,
        accept_result,
        handle_failure,
        semantic: bool = False,
    ) -> None:
        done_futures: set[concurrent.futures.Future] = set()
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
                    chunk_lifecycles[planned.index] = self._project_chunk_event(
                        chunk_lifecycles[planned.index],
                        TranslationChunkLifecycleEvent.TRANSLATION_COMPLETED,
                        chunk_projector,
                        planned,
                        report.total_chunks,
                        semantic=semantic,
                    )
                    accept_result(planned, result)
                except Exception as exc:
                    logger.exception(
                        "%schunk处理失败: chunk=%s entries=%s",
                        "语义" if semantic else "",
                        planned.index + 1,
                        [entry.index for entry in planned.entries],
                    )
                    handle_failure(planned, exc)
                finally:
                    self._save_chunk_progress(
                        planned,
                        chunk_lifecycles,
                        chunk_projector,
                        progress_contract,
                        subtitle,
                        translated_entries,
                        run_ledger,
                        task_store,
                        task_id,
                        report,
                        semantic=semantic,
                    )

    def _save_chunk_progress(
        self,
        planned: PlannedChunk,
        chunk_lifecycles: dict[int, TranslationChunkLifecycle],
        chunk_projector: ChunkLifecycleProjector,
        progress_contract: ProgressContract,
        subtitle: Subtitle,
        translated_entries: list,
        run_ledger: RunLedger,
        task_store: TranslationTaskStore,
        task_id: str,
        report: TranslationReport,
        *,
        semantic: bool = False,
    ) -> None:
        report.completed_chunks += 1
        report.processed_entries = run_ledger.processed_entry_count(translated_entries)
        run_ledger.save_task_state(task_store, task_id, subtitle, report, translated_entries)
        chunk_state = terminal_chunk_state_for_planned(report, planned)
        chunk_lifecycles[planned.index] = finish_chunk_lifecycle(
            chunk_lifecycles[planned.index],
            chunk_state,
        )
        chunk_projector.project(
            chunk_lifecycles[planned.index].state,
            planned.entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            semantic=semantic,
            emit_progress=False,
        )
        progress_contract.task_state_saved(
            planned.entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            semantic=semantic,
            warning=bool(planned.entries and any(entry.needs_retranslation for entry in planned.entries)),
            fallback=any(failed.chunk_index == planned.index for failed in report.failed_chunks),
        )

    def _project_chunk_event(
        self,
        lifecycle: TranslationChunkLifecycle,
        event: TranslationChunkLifecycleEvent,
        chunk_projector: ChunkLifecycleProjector,
        planned: PlannedChunk,
        total_chunks: int,
        *,
        semantic: bool = False,
    ) -> TranslationChunkLifecycle:
        lifecycle = lifecycle.apply(event)
        chunk_projector.project(
            lifecycle.state,
            planned.entries,
            chunk_index=planned.index,
            total_chunks=total_chunks,
            semantic=semantic,
            emit_progress=False,
        )
        return lifecycle


def terminal_chunk_state_for_planned(report: TranslationReport, planned: PlannedChunk) -> TranslationChunkState:
    if any(failed.chunk_index == planned.index for failed in report.failed_chunks):
        return TranslationChunkState.FAILED
    if any(entry.needs_retranslation for entry in planned.entries):
        return TranslationChunkState.ACCEPTED_WITH_WARNINGS
    return TranslationChunkState.ACCEPTED


def finish_chunk_lifecycle(
    lifecycle: TranslationChunkLifecycle,
    target_state: TranslationChunkState,
) -> TranslationChunkLifecycle:
    if target_state is TranslationChunkState.FAILED:
        return lifecycle.apply(TranslationChunkLifecycleEvent.FAILED)
    return lifecycle.apply(
        TranslationChunkLifecycleEvent.ACCEPTED_WITH_WARNINGS
        if target_state is TranslationChunkState.ACCEPTED_WITH_WARNINGS
        else TranslationChunkLifecycleEvent.ACCEPTED
    )
