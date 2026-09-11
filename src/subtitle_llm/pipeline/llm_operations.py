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


@dataclass(frozen=True)
class RunUsageSnapshot:
    """运行级累计用量快照：每次 LLM 调用返回时随进度事件发出，

    桌面端据此展示实时 token/s，无需自行去重累计。"""

    call_count: int
    completion_tokens: int
    total_tokens: int
    call_duration_ms: int

    def to_dict(self) -> dict[str, int]:
        return {
            "call_count": self.call_count,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "call_duration_ms": self.call_duration_ms,
        }


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
        self._run_call_count = 0
        self._run_completion_tokens = 0
        self._run_total_tokens = 0
        self._run_call_duration_ms = 0

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
        run_usage = self._accumulate_run_usage(completion.usage, duration_ms)
        self.emit_chunk_progress(
            stage,
            chunk_visual_status(stage),
            f"{chunk_stage_label(stage)}返回，耗时 {duration_ms / 1000:.1f}s",
            chunk,
            chunk_index,
            usage=completion.usage,
            duration_ms=duration_ms,
            run_usage=run_usage,
        )
        return LlmOperationResult(completion=completion, duration_ms=duration_ms)

    def _accumulate_run_usage(self, usage: CompletionUsage, duration_ms: int) -> RunUsageSnapshot:
        # 片段线程池并发调用：GIL 下自增是安全的展示级指标，允许微小竞争
        self._run_call_count += 1
        self._run_completion_tokens += usage.completion_tokens
        self._run_total_tokens += usage.total_tokens
        self._run_call_duration_ms += duration_ms
        return RunUsageSnapshot(
            call_count=self._run_call_count,
            completion_tokens=self._run_completion_tokens,
            total_tokens=self._run_total_tokens,
            call_duration_ms=self._run_call_duration_ms,
        )

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
        run_usage: RunUsageSnapshot | None = None,
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
            run_usage=run_usage.to_dict() if run_usage else None,
        )


def elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))
