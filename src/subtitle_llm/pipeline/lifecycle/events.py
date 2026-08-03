from __future__ import annotations

from enum import Enum


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class TranslationTaskLifecycleEvent(_StringEnum):
    PREPARE_INPUT_STARTED = "prepare_input_started"
    PREPARE_INPUT_COMPLETED = "prepare_input_completed"
    PREPARE_TRANSLATION_STARTED = "prepare_translation_started"
    PREPARE_TRANSLATION_COMPLETED = "prepare_translation_completed"
    PROCESS_CHUNKS_STARTED = "process_chunks_started"
    PROCESS_CHUNKS_COMPLETED = "process_chunks_completed"
    FINALIZE_OUTPUT_STARTED = "finalize_output_started"
    FINALIZE_OUTPUT_COMPLETED = "finalize_output_completed"
    FINALIZE_OUTPUT_COMPLETED_WITH_WARNINGS = "finalize_output_completed_with_warnings"
    FAILED = "failed"


class TranslationChunkLifecycleEvent(_StringEnum):
    QUEUED = "queued"
    TRANSLATION_STARTED = "translation_started"
    TRANSLATION_COMPLETED = "translation_completed"
    QUALITY_CHECK_STARTED = "quality_check_started"
    QUALITY_PASSED = "quality_passed"
    REPAIR_REQUIRED = "repair_required"
    REPAIR_COMPLETED = "repair_completed"
    REVIEW_REQUIRED = "review_required"
    REVIEW_ACCEPTED = "review_accepted"
    ACCEPTED = "accepted"
    ACCEPTED_WITH_WARNINGS = "accepted_with_warnings"
    FAILED = "failed"


def coerce_task_event(value: TranslationTaskLifecycleEvent | str) -> TranslationTaskLifecycleEvent:
    if isinstance(value, TranslationTaskLifecycleEvent):
        return value
    try:
        return TranslationTaskLifecycleEvent(value)
    except ValueError as exc:
        allowed = ", ".join(event.value for event in TranslationTaskLifecycleEvent)
        raise ValueError(f"未知翻译任务生命周期事件：{value!r}，允许值：{allowed}") from exc


def coerce_chunk_event(value: TranslationChunkLifecycleEvent | str) -> TranslationChunkLifecycleEvent:
    if isinstance(value, TranslationChunkLifecycleEvent):
        return value
    try:
        return TranslationChunkLifecycleEvent(value)
    except ValueError as exc:
        allowed = ", ".join(event.value for event in TranslationChunkLifecycleEvent)
        raise ValueError(f"未知翻译片段生命周期事件：{value!r}，允许值：{allowed}") from exc
