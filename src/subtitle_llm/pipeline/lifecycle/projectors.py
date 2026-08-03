from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.pipeline.lifecycle.states import (
    TranslationChunkState,
    TranslationTaskState,
    coerce_chunk_state,
    coerce_task_state,
)
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.task_store import TranslationTaskStore
from subtitle_llm.progress_contract import ProgressContract


TASK_REPORT_STAGES = {
    TranslationTaskState.CREATED: "初始化",
    TranslationTaskState.PREPARING_INPUT: "准备输入",
    TranslationTaskState.PREPARING_TRANSLATION: "准备翻译",
    TranslationTaskState.PROCESSING_CHUNKS: "处理片段",
    TranslationTaskState.FINALIZING_OUTPUT: "生成结果",
    TranslationTaskState.COMPLETED: "完成",
    TranslationTaskState.COMPLETED_WITH_WARNINGS: "完成（有风险）",
    TranslationTaskState.FAILED: "失败",
}


@dataclass(frozen=True)
class TaskLifecycleProjector:
    task_store: TranslationTaskStore
    task_id: str
    report: TranslationReport
    progress_contract: ProgressContract | None = None

    def project(
        self,
        state: TranslationTaskState | str,
        *,
        error_summary: str | None = None,
        total_chunks: int | None = None,
        emit_progress: bool = False,
    ) -> None:
        task_state = coerce_task_state(state)
        self.task_store.update_status(self.task_id, task_state, error_summary=error_summary)
        self.report.stage = TASK_REPORT_STAGES[task_state]
        if not emit_progress or self.progress_contract is None:
            return
        if task_state is TranslationTaskState.FINALIZING_OUTPUT:
            self.progress_contract.finalizing_subtitle()
        elif task_state in {TranslationTaskState.COMPLETED, TranslationTaskState.COMPLETED_WITH_WARNINGS}:
            self.progress_contract.complete(total_chunks=total_chunks if total_chunks is not None else self.report.total_chunks)


@dataclass(frozen=True)
class ChunkLifecycleProjector:
    task_store: TranslationTaskStore
    task_id: str
    progress_contract: ProgressContract | None = None

    def project(
        self,
        state: TranslationChunkState | str,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
        semantic: bool = False,
        diagnosis: dict[str, Any] | None = None,
        last_trace_id: str | None = None,
        error: Exception | str | None = None,
        emit_progress: bool = True,
    ) -> None:
        chunk_state = coerce_chunk_state(state)
        self.task_store.save_chunk_state(
            self.task_id,
            chunk_index=chunk_index,
            entry_indices=[entry.index for entry in entries],
            status=chunk_state,
            diagnosis=diagnosis,
            last_trace_id=last_trace_id,
        )
        if not emit_progress or self.progress_contract is None:
            return
        if chunk_state is TranslationChunkState.QUEUED:
            self.progress_contract.chunk_queued(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                semantic=semantic,
            )
        elif chunk_state in {TranslationChunkState.ACCEPTED, TranslationChunkState.ACCEPTED_WITH_WARNINGS}:
            self.progress_contract.chunk_accepted(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                semantic=semantic,
                warning=chunk_state is TranslationChunkState.ACCEPTED_WITH_WARNINGS,
            )
        elif chunk_state is TranslationChunkState.FAILED:
            self.progress_contract.chunk_failed(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                semantic=semantic,
                error=error or "chunk failed",
            )
