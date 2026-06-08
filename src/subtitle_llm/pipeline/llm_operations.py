from __future__ import annotations

import time
from dataclasses import dataclass

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.llm.types import ChatClient, CompletionResult, CompletionUsage
from subtitle_llm.pipeline.llm_trace import LlmTraceRecorder
from subtitle_llm.progress_contract import (
    ProgressContract,
    chunk_position_text,
    chunk_stage_label,
    chunk_visual_status,
)
from subtitle_llm.progress_events import ProgressEmitter
from subtitle_llm.settings import ModelConfig


@dataclass(frozen=True)
class LlmOperationResult:
    completion: CompletionResult
    duration_ms: int


class LlmOperationRunner:
    def __init__(
        self,
        client: ChatClient,
        model_config: ModelConfig,
        trace_recorder: LlmTraceRecorder | None = None,
        total_chunks: int | None = None,
        progress: ProgressEmitter | None = None,
    ):
        self.client = client
        self.model_config = model_config
        self.trace_recorder = trace_recorder
        self.total_chunks = total_chunks
        self.progress = progress

    def create_completion(
        self,
        prompt: str,
        *,
        stage: str,
        chunk: list[SubtitleEntry] | None = None,
        chunk_index: int | None = None,
    ) -> LlmOperationResult:
        started_at = time.perf_counter()
        self.emit_chunk_progress(
            stage,
            chunk_visual_status(stage),
            f"正在{chunk_stage_label(stage)}" + chunk_position_text(chunk_index, self.total_chunks),
            chunk,
            chunk_index,
        )
        try:
            completion = self.client.create_completion(self.model_config, [{"role": "user", "content": prompt}])
        except Exception as exc:
            duration_ms = elapsed_ms(started_at)
            self.emit_chunk_progress(
                stage,
                "failed",
                f"{chunk_stage_label(stage)}失败：{exc}",
                chunk,
                chunk_index,
                duration_ms=duration_ms,
            )
            if self.trace_recorder is not None:
                self.trace_recorder.record_call(
                    stage="llm-error",
                    prompt=prompt,
                    response="",
                    model_config=self.model_config,
                    usage=CompletionUsage(),
                    duration_ms=duration_ms,
                    chunk=chunk,
                    chunk_index=chunk_index,
                    total_chunks=self.total_chunks,
                    expected_count=len(chunk) if chunk is not None else None,
                    status="failed",
                    error=str(exc),
                )
            raise

        duration_ms = elapsed_ms(started_at)
        self.emit_chunk_progress(
            stage,
            chunk_visual_status(stage),
            f"{chunk_stage_label(stage)}返回，耗时 {duration_ms / 1000:.1f}s",
            chunk,
            chunk_index,
            usage=completion.usage,
            duration_ms=duration_ms,
        )
        return LlmOperationResult(completion=completion, duration_ms=duration_ms)

    def record_trace(
        self,
        *,
        stage: str,
        prompt: str,
        response: str,
        usage: CompletionUsage,
        duration_ms: int,
        chunk: list[SubtitleEntry],
        chunk_index: int | None = None,
        processed_translation: str | None = None,
        error: str | None = None,
    ) -> str | None:
        if self.trace_recorder is None:
            return None
        return self.trace_recorder.record_call(
            stage=stage,
            prompt=prompt,
            response=response,
            model_config=self.model_config,
            usage=usage,
            duration_ms=duration_ms,
            chunk=chunk,
            chunk_index=chunk_index,
            total_chunks=self.total_chunks,
            processed_translation=processed_translation,
            expected_count=len(chunk),
            error=error,
        )

    def emit_chunk_progress(
        self,
        detail: str,
        visual_status: str,
        message: str,
        chunk: list[SubtitleEntry] | None,
        chunk_index: int | None,
        *,
        usage: CompletionUsage | None = None,
        duration_ms: int | None = None,
        trace_id: str | None = None,
    ) -> None:
        if self.progress is None or chunk is None:
            return
        ProgressContract(self.progress).llm_chunk_event(
            detail=detail,
            visual_status=visual_status,
            message=message,
            entries=chunk,
            chunk_index=chunk_index,
            total_chunks=self.total_chunks,
            model=self.model_config,
            usage=usage,
            trace_id=trace_id,
            duration_ms=duration_ms,
        )


def elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))
