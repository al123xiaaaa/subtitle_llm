"""翻译片段并发协调器：只负责线程池、片段入队与生命周期骨架事件。

片段从「译完」到「终态」的全部策略（诊断、修复、复核）在
chunk_acceptance.ChunkAcceptance 里；这里不再持有任何编排决策。
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from dataclasses import dataclass
from typing import Callable

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


@dataclass
class _ChunkTaskContext:
    """单个片段任务在 worker 线程内完成「翻译 → 接受 → 收尾」所需的共享状态。"""

    chunk_lifecycles: dict[int, TranslationChunkLifecycle]
    chunk_projector: ChunkLifecycleProjector
    progress_contract: ProgressContract
    subtitle: Subtitle
    translated_entries: list
    run_ledger: RunLedger
    task_store: TranslationTaskStore
    task_id: str
    report: TranslationReport
    # completed_chunks += 1 和任务状态快照写库需要互斥，避免并发读改写丢更新
    report_lock: threading.Lock
    semantic: bool = False


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
        context = self._task_context(
            chunk_lifecycles,
            chunk_projector,
            progress_contract,
            subtitle,
            translated_entries,
            run_ledger,
            task_store,
            task_id,
            report,
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.threads) as executor:
            self._run_chunk_tasks(
                executor,
                planned_chunks,
                context,
                chunk_projector,
                report,
                translate=lambda planned: translator.translate_and_refine(
                    planned.entries,
                    acceptance.context,
                    acceptance.target_language,
                    planned.boundary_context,
                    planned.index,
                    refine_translation=acceptance.refine_translation,
                ),
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
        context = self._task_context(
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
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.threads) as executor:
            self._run_chunk_tasks(
                executor,
                planned_chunks,
                context,
                chunk_projector,
                report,
                translate=lambda planned: acceptance.translate_semantic(
                    planned,
                    semantic_unit_by_index,
                ),
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

    def _task_context(
        self,
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
    ) -> _ChunkTaskContext:
        return _ChunkTaskContext(
            chunk_lifecycles=chunk_lifecycles,
            chunk_projector=chunk_projector,
            progress_contract=progress_contract,
            subtitle=subtitle,
            translated_entries=translated_entries,
            run_ledger=run_ledger,
            task_store=task_store,
            task_id=task_id,
            report=report,
            report_lock=threading.Lock(),
            semantic=semantic,
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

    def _run_chunk_tasks(
        self,
        executor: concurrent.futures.ThreadPoolExecutor,
        planned_chunks: list[PlannedChunk],
        context: _ChunkTaskContext,
        chunk_projector: ChunkLifecycleProjector,
        report: TranslationReport,
        *,
        translate: Callable[[PlannedChunk], object],
        accept_result: Callable[[PlannedChunk, object], None],
        handle_failure: Callable[[PlannedChunk, BaseException], None],
    ) -> None:
        future_to_chunk: dict[concurrent.futures.Future, PlannedChunk] = {}
        for planned in planned_chunks:
            chunk_lifecycles = context.chunk_lifecycles
            chunk_lifecycles[planned.index] = self._project_chunk_event(
                chunk_lifecycles[planned.index],
                TranslationChunkLifecycleEvent.TRANSLATION_STARTED,
                chunk_projector,
                planned,
                report.total_chunks,
                semantic=context.semantic,
            )
            future_to_chunk[
                executor.submit(
                    self._process_chunk_task,
                    planned,
                    context,
                    translate=translate,
                    accept_result=accept_result,
                    handle_failure=handle_failure,
                )
            ] = planned
        # 协调线程不再串行消费接受结果；abort 模式下 handle_failure 的重抛
        # 在这里浮出，其余异常已在任务内兜底。
        for future in concurrent.futures.as_completed(future_to_chunk):
            future.result()

    def _process_chunk_task(
        self,
        planned: PlannedChunk,
        context: _ChunkTaskContext,
        *,
        translate: Callable[[PlannedChunk], object],
        accept_result: Callable[[PlannedChunk, object], None],
        handle_failure: Callable[[PlannedChunk, BaseException], None],
    ) -> None:
        """worker 线程内的完整片段管线：翻译 → 接受（诊断/修复/复核）→ 收尾。

        接受与翻译同线程执行，长耗时的 TUI 人工复核或专名修复只占用本
        片段的 worker，不再阻塞其他片段的接受；TUI 复核窗口由
        TuiReviewPort 内部锁保证同一时刻只弹一个。
        """
        try:
            result = translate(planned)
            context.chunk_lifecycles[planned.index] = self._project_chunk_event(
                context.chunk_lifecycles[planned.index],
                TranslationChunkLifecycleEvent.TRANSLATION_COMPLETED,
                context.chunk_projector,
                planned,
                context.report.total_chunks,
                semantic=context.semantic,
            )
            accept_result(planned, result)
        except Exception as exc:
            logger.exception(
                "%schunk处理失败: chunk=%s entries=%s",
                "语义" if context.semantic else "",
                planned.index + 1,
                [entry.index for entry in planned.entries],
            )
            handle_failure(planned, exc)
        finally:
            self._save_chunk_progress(planned, context)

    def _save_chunk_progress(self, planned: PlannedChunk, context: _ChunkTaskContext) -> None:
        report = context.report
        with context.report_lock:
            report.completed_chunks += 1
            report.processed_entries = context.run_ledger.processed_entry_count(
                context.translated_entries
            )
            context.run_ledger.save_task_state(
                context.task_store,
                context.task_id,
                context.subtitle,
                report,
                context.translated_entries,
            )
            chunk_state = terminal_chunk_state_for_planned(report, planned)
            context.chunk_lifecycles[planned.index] = finish_chunk_lifecycle(
                context.chunk_lifecycles[planned.index],
                chunk_state,
            )
            context.chunk_projector.project(
                context.chunk_lifecycles[planned.index].state,
                planned.entries,
                chunk_index=planned.index,
                total_chunks=report.total_chunks,
                semantic=context.semantic,
                emit_progress=False,
            )
        context.progress_contract.task_state_saved(
            planned.entries,
            chunk_index=planned.index,
            total_chunks=report.total_chunks,
            semantic=context.semantic,
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
