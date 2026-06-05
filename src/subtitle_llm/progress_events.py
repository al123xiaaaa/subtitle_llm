from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.llm.types import CompletionUsage
from subtitle_llm.settings import ModelConfig

PROGRESS_EVENT_PREFIX = "SUBTITLE_LLM_PROGRESS "

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProgressEmitter:
    command: str
    started_at: float = 0

    def __post_init__(self) -> None:
        if self.started_at <= 0:
            object.__setattr__(self, "started_at", time.perf_counter())

    def emit(
        self,
        *,
        stage: str,
        detail: str,
        status: str = "running",
        label: str = "",
        message: str = "",
        chunk: dict[str, Any] | None = None,
        model: dict[str, Any] | ModelConfig | None = None,
        usage: dict[str, Any] | CompletionUsage | None = None,
        trace_id: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        payload = compact_dict(
            {
                "command": self.command,
                "stage": stage,
                "detail": detail,
                "status": status,
                "label": label,
                "message": message,
                "elapsed_ms": elapsed_ms(self.started_at),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "chunk": chunk,
                "model": model_payload(model),
                "usage": usage_payload(usage),
                "trace_id": trace_id,
                **extra,
            }
        )
        line = PROGRESS_EVENT_PREFIX + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        print(line, flush=True)
        logger.info("进度事件: %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return payload


def chunk_payload(
    entries: list[SubtitleEntry],
    *,
    chunk_index: int | None,
    total_chunks: int | None,
    status: str,
    detail: str,
    issue_summary: str = "",
    reason: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "index": chunk_index + 1 if chunk_index is not None else None,
        "total": total_chunks,
        "status": status,
        "detail": detail,
        "issue_summary": issue_summary,
        "reason": reason,
    }
    if entries:
        payload.update(
            {
                "entry_start": entries[0].index,
                "entry_end": entries[-1].index,
                "entry_count": len(entries),
            }
        )
    return compact_dict(payload)


def model_payload(model: dict[str, Any] | ModelConfig | None) -> dict[str, Any] | None:
    if model is None:
        return None
    if isinstance(model, dict):
        return compact_dict(model)
    return compact_dict(
        {
            "provider": model.provider.value,
            "name": model.model,
            "endpoint": model.endpoint,
        }
    )


def usage_payload(usage: dict[str, Any] | CompletionUsage | None) -> dict[str, int] | None:
    if usage is None:
        return None
    data = usage.to_dict() if isinstance(usage, CompletionUsage) else usage
    return {
        "prompt_tokens": int(data.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(data.get("completion_tokens", 0) or 0),
        "total_tokens": int(data.get("total_tokens", 0) or 0),
    }


def elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def compact_dict(data: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in data.items():
        if value is None or value == "":
            continue
        if isinstance(value, dict):
            nested = compact_dict(value)
            if nested:
                compacted[key] = nested
            continue
        compacted[key] = value
    return compacted
