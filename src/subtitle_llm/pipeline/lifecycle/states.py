from __future__ import annotations

from enum import Enum


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class TranslationTaskState(_StringEnum):
    CREATED = "created"
    PREPARING_INPUT = "preparing_input"
    PREPARING_TRANSLATION = "preparing_translation"
    PROCESSING_CHUNKS = "processing_chunks"
    FINALIZING_OUTPUT = "finalizing_output"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"


class TranslationChunkState(_StringEnum):
    QUEUED = "queued"
    TRANSLATING = "translating"
    CHECKING_QUALITY = "checking_quality"
    REPAIRING = "repairing"
    REVIEWING = "reviewing"
    ACCEPTED = "accepted"
    ACCEPTED_WITH_WARNINGS = "accepted_with_warnings"
    FAILED = "failed"


TERMINAL_TASK_STATES = frozenset({
    TranslationTaskState.COMPLETED,
    TranslationTaskState.COMPLETED_WITH_WARNINGS,
})

RESUMABLE_TASK_STATES = frozenset({
    TranslationTaskState.CREATED,
    TranslationTaskState.PREPARING_INPUT,
    TranslationTaskState.PREPARING_TRANSLATION,
    TranslationTaskState.PROCESSING_CHUNKS,
    TranslationTaskState.FINALIZING_OUTPUT,
    TranslationTaskState.FAILED,
})

TERMINAL_CHUNK_STATES = frozenset({
    TranslationChunkState.ACCEPTED,
    TranslationChunkState.ACCEPTED_WITH_WARNINGS,
    TranslationChunkState.FAILED,
})


def coerce_task_state(value: TranslationTaskState | str) -> TranslationTaskState:
    if isinstance(value, TranslationTaskState):
        return value
    try:
        return TranslationTaskState(value)
    except ValueError as exc:
        allowed = ", ".join(state.value for state in TranslationTaskState)
        raise ValueError(f"未知翻译任务状态：{value!r}，允许值：{allowed}") from exc


def coerce_chunk_state(value: TranslationChunkState | str) -> TranslationChunkState:
    if isinstance(value, TranslationChunkState):
        return value
    try:
        return TranslationChunkState(value)
    except ValueError as exc:
        allowed = ", ".join(state.value for state in TranslationChunkState)
        raise ValueError(f"未知翻译片段状态：{value!r}，允许值：{allowed}") from exc
