from __future__ import annotations

from dataclasses import dataclass, replace

from subtitle_llm.pipeline.lifecycle.events import (
    TranslationChunkLifecycleEvent,
    TranslationTaskLifecycleEvent,
    coerce_chunk_event,
    coerce_task_event,
)
from subtitle_llm.pipeline.lifecycle.states import (
    TERMINAL_CHUNK_STATES,
    TERMINAL_TASK_STATES,
    TranslationChunkState,
    TranslationTaskState,
    coerce_chunk_state,
    coerce_task_state,
)


class LifecycleTransitionError(ValueError):
    pass


TASK_TRANSITIONS: dict[tuple[TranslationTaskState, TranslationTaskLifecycleEvent], TranslationTaskState] = {
    (TranslationTaskState.CREATED, TranslationTaskLifecycleEvent.PREPARE_INPUT_STARTED): (
        TranslationTaskState.PREPARING_INPUT
    ),
    (TranslationTaskState.CREATED, TranslationTaskLifecycleEvent.PREPARE_INPUT_COMPLETED): (
        TranslationTaskState.PREPARING_TRANSLATION
    ),
    (TranslationTaskState.PREPARING_INPUT, TranslationTaskLifecycleEvent.PREPARE_INPUT_COMPLETED): (
        TranslationTaskState.PREPARING_TRANSLATION
    ),
    (TranslationTaskState.PREPARING_INPUT, TranslationTaskLifecycleEvent.PREPARE_TRANSLATION_STARTED): (
        TranslationTaskState.PREPARING_TRANSLATION
    ),
    (TranslationTaskState.FAILED, TranslationTaskLifecycleEvent.PREPARE_TRANSLATION_STARTED): (
        TranslationTaskState.PREPARING_TRANSLATION
    ),
    (TranslationTaskState.PREPARING_TRANSLATION, TranslationTaskLifecycleEvent.PREPARE_TRANSLATION_COMPLETED): (
        TranslationTaskState.PROCESSING_CHUNKS
    ),
    (TranslationTaskState.PREPARING_TRANSLATION, TranslationTaskLifecycleEvent.PROCESS_CHUNKS_STARTED): (
        TranslationTaskState.PROCESSING_CHUNKS
    ),
    (TranslationTaskState.PROCESSING_CHUNKS, TranslationTaskLifecycleEvent.PROCESS_CHUNKS_COMPLETED): (
        TranslationTaskState.FINALIZING_OUTPUT
    ),
    (TranslationTaskState.PROCESSING_CHUNKS, TranslationTaskLifecycleEvent.FINALIZE_OUTPUT_STARTED): (
        TranslationTaskState.FINALIZING_OUTPUT
    ),
    (TranslationTaskState.FINALIZING_OUTPUT, TranslationTaskLifecycleEvent.FINALIZE_OUTPUT_COMPLETED): (
        TranslationTaskState.COMPLETED
    ),
    (TranslationTaskState.FINALIZING_OUTPUT, TranslationTaskLifecycleEvent.FINALIZE_OUTPUT_COMPLETED_WITH_WARNINGS): (
        TranslationTaskState.COMPLETED_WITH_WARNINGS
    ),
}

CHUNK_TRANSITIONS: dict[tuple[TranslationChunkState, TranslationChunkLifecycleEvent], TranslationChunkState] = {
    (TranslationChunkState.QUEUED, TranslationChunkLifecycleEvent.QUEUED): TranslationChunkState.QUEUED,
    (TranslationChunkState.QUEUED, TranslationChunkLifecycleEvent.TRANSLATION_STARTED): (
        TranslationChunkState.TRANSLATING
    ),
    (TranslationChunkState.TRANSLATING, TranslationChunkLifecycleEvent.TRANSLATION_COMPLETED): (
        TranslationChunkState.CHECKING_QUALITY
    ),
    (TranslationChunkState.TRANSLATING, TranslationChunkLifecycleEvent.QUALITY_CHECK_STARTED): (
        TranslationChunkState.CHECKING_QUALITY
    ),
    (TranslationChunkState.CHECKING_QUALITY, TranslationChunkLifecycleEvent.QUALITY_PASSED): (
        TranslationChunkState.ACCEPTED
    ),
    (TranslationChunkState.CHECKING_QUALITY, TranslationChunkLifecycleEvent.REPAIR_REQUIRED): (
        TranslationChunkState.REPAIRING
    ),
    (TranslationChunkState.CHECKING_QUALITY, TranslationChunkLifecycleEvent.REVIEW_REQUIRED): (
        TranslationChunkState.REVIEWING
    ),
    (TranslationChunkState.CHECKING_QUALITY, TranslationChunkLifecycleEvent.ACCEPTED): (
        TranslationChunkState.ACCEPTED
    ),
    (TranslationChunkState.CHECKING_QUALITY, TranslationChunkLifecycleEvent.ACCEPTED_WITH_WARNINGS): (
        TranslationChunkState.ACCEPTED_WITH_WARNINGS
    ),
    (TranslationChunkState.REPAIRING, TranslationChunkLifecycleEvent.REPAIR_COMPLETED): (
        TranslationChunkState.CHECKING_QUALITY
    ),
    (TranslationChunkState.REPAIRING, TranslationChunkLifecycleEvent.ACCEPTED): TranslationChunkState.ACCEPTED,
    (TranslationChunkState.REPAIRING, TranslationChunkLifecycleEvent.ACCEPTED_WITH_WARNINGS): (
        TranslationChunkState.ACCEPTED_WITH_WARNINGS
    ),
    (TranslationChunkState.REVIEWING, TranslationChunkLifecycleEvent.REVIEW_ACCEPTED): (
        TranslationChunkState.ACCEPTED
    ),
    # 复核结束后协调器统一应用终态事件（ACCEPTED / ACCEPTED_WITH_WARNINGS）。
    (TranslationChunkState.REVIEWING, TranslationChunkLifecycleEvent.ACCEPTED): TranslationChunkState.ACCEPTED,
    (TranslationChunkState.REVIEWING, TranslationChunkLifecycleEvent.ACCEPTED_WITH_WARNINGS): (
        TranslationChunkState.ACCEPTED_WITH_WARNINGS
    ),
}


@dataclass(frozen=True)
class TranslationTaskLifecycle:
    state: TranslationTaskState = TranslationTaskState.CREATED

    @classmethod
    def from_state(cls, state: TranslationTaskState | str) -> "TranslationTaskLifecycle":
        return cls(coerce_task_state(state))

    def apply(self, event: TranslationTaskLifecycleEvent | str) -> "TranslationTaskLifecycle":
        event = coerce_task_event(event)
        if self.state in TERMINAL_TASK_STATES:
            raise LifecycleTransitionError(f"翻译任务已结束，不能再接收事件：{self.state.value} -> {event.value}")
        if event is TranslationTaskLifecycleEvent.FAILED:
            return replace(self, state=TranslationTaskState.FAILED)
        next_state = TASK_TRANSITIONS.get((self.state, event))
        if next_state is None:
            raise LifecycleTransitionError(f"非法翻译任务状态转移：{self.state.value} -> {event.value}")
        return replace(self, state=next_state)


@dataclass(frozen=True)
class TranslationChunkLifecycle:
    state: TranslationChunkState = TranslationChunkState.QUEUED

    @classmethod
    def from_state(cls, state: TranslationChunkState | str) -> "TranslationChunkLifecycle":
        return cls(coerce_chunk_state(state))

    def apply(self, event: TranslationChunkLifecycleEvent | str) -> "TranslationChunkLifecycle":
        event = coerce_chunk_event(event)
        if self.state in TERMINAL_CHUNK_STATES:
            raise LifecycleTransitionError(f"翻译片段已结束，不能再接收事件：{self.state.value} -> {event.value}")
        if event is TranslationChunkLifecycleEvent.FAILED:
            return replace(self, state=TranslationChunkState.FAILED)
        next_state = CHUNK_TRANSITIONS.get((self.state, event))
        if next_state is None:
            raise LifecycleTransitionError(f"非法翻译片段状态转移：{self.state.value} -> {event.value}")
        return replace(self, state=next_state)
