from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.llm.types import CompletionUsage
from subtitle_llm.runtime_logging import RUN_LOG_ENV
from subtitle_llm.settings import ModelConfig

TraceStatus = Literal["ok", "suspicious", "failed"]

PLACEHOLDER_MARKERS = (
    "Translation missing line",
    "Translated text for entry",
    "Translated text",
    "翻译缺失",
)

SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s\"']+)"),
    re.compile(r"(?i)(\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*[\"']?)([^\"'\s,}]+)"),
    re.compile(r"(?i)(\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\b\s*[:=]\s*[\"']?)([^\"'\s,}]+)"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{8,})\b"),
]


@dataclass(frozen=True)
class ParseSummary:
    expected_count: int
    parsed_count: int
    missing_count: int
    placeholder_count: int
    output_index_lines: int

    def to_dict(self) -> dict[str, int]:
        return {
            "expected_count": self.expected_count,
            "parsed_count": self.parsed_count,
            "missing_count": self.missing_count,
            "placeholder_count": self.placeholder_count,
            "output_index_lines": self.output_index_lines,
        }


@dataclass
class _TraceRecord:
    trace_id: str
    stem: str
    status: TraceStatus
    metadata: dict[str, Any]
    json_path: Path
    prompt_path: Path
    response_path: Path


class LlmTraceRecorder:
    """Persist prompt/response trace files for every pipeline LLM call."""

    _status_rank = {"ok": 0, "suspicious": 1, "failed": 2}

    def __init__(self, trace_dir: str | Path):
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._counter = 0
        self._records: dict[str, _TraceRecord] = {}

    @classmethod
    def for_run(cls, output_file: str | Path) -> "LlmTraceRecorder":
        return cls(default_llm_trace_dir(output_file))

    def record_call(
        self,
        *,
        stage: str,
        prompt: str,
        response: str,
        model_config: ModelConfig,
        usage: CompletionUsage | None,
        duration_ms: int,
        chunk: list[SubtitleEntry] | None = None,
        chunk_index: int | None = None,
        total_chunks: int | None = None,
        processed_translation: str | None = None,
        expected_count: int | None = None,
        status: TraceStatus | None = None,
        error: str | None = None,
    ) -> str:
        parse_summary = summarize_processed_translation(
            processed_translation,
            expected_count if expected_count is not None else (len(chunk) if chunk is not None else 0),
        )
        next_status = status or status_from_parse(parse_summary, error=error)

        with self._lock:
            self._counter += 1
            trace_id = f"{self._counter:06d}"
            stem = self._build_stem(trace_id, stage, chunk_index)
            record = self._create_record(
                trace_id=trace_id,
                stem=stem,
                status=next_status,
                prompt=prompt,
                response=response,
                metadata={
                    "trace_id": trace_id,
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "stage": stage,
                    "status": next_status,
                    "provider": model_config.provider.value,
                    "model": model_config.model,
                    "endpoint": model_config.endpoint,
                    "chunk": chunk_summary(chunk, chunk_index, total_chunks),
                    "usage": usage.to_dict() if usage else CompletionUsage().to_dict(),
                    "duration_ms": duration_ms,
                    "parse": parse_summary.to_dict() if parse_summary else None,
                    "quality": None,
                    "response_shape": response_shape(response),
                    "error": error,
                    "files": {},
                },
            )
            self._records[trace_id] = record
            self._write_record(record)
            return trace_id

    def update_quality(
        self,
        trace_id: str | None,
        diagnosis: Any,
        *,
        status: TraceStatus | None = None,
    ) -> None:
        if not trace_id:
            return

        with self._lock:
            record = self._records.get(trace_id)
            if record is None:
                return
            record.metadata["quality"] = quality_summary(diagnosis)
            next_status = status or status_from_quality(diagnosis)
            if next_status is not None:
                self._set_status(record, self._max_status(record.status, next_status))
            self._write_record(record)

    def _create_record(
        self,
        *,
        trace_id: str,
        stem: str,
        status: TraceStatus,
        prompt: str,
        response: str,
        metadata: dict[str, Any],
    ) -> _TraceRecord:
        base = f"{stem}-{status}"
        prompt_path = self.trace_dir / f"{base}-prompt.txt"
        response_path = self.trace_dir / f"{base}-response.txt"
        json_path = self.trace_dir / f"{base}.json"
        prompt_path.write_text(redact_secrets(prompt), encoding="utf-8")
        response_path.write_text(redact_secrets(response), encoding="utf-8")
        metadata["files"] = {
            "prompt": prompt_path.name,
            "response": response_path.name,
        }
        return _TraceRecord(
            trace_id=trace_id,
            stem=stem,
            status=status,
            metadata=metadata,
            json_path=json_path,
            prompt_path=prompt_path,
            response_path=response_path,
        )

    def _write_record(self, record: _TraceRecord) -> None:
        record.metadata["files"] = {
            "prompt": record.prompt_path.name,
            "response": record.response_path.name,
        }
        record.json_path.write_text(
            json.dumps(record.metadata, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _set_status(self, record: _TraceRecord, status: TraceStatus) -> None:
        if record.status == status:
            record.metadata["status"] = status
            return

        old_json_path = record.json_path
        base = f"{record.stem}-{status}"
        new_prompt_path = self.trace_dir / f"{base}-prompt.txt"
        new_response_path = self.trace_dir / f"{base}-response.txt"
        new_json_path = self.trace_dir / f"{base}.json"
        record.prompt_path.replace(new_prompt_path)
        record.response_path.replace(new_response_path)
        if old_json_path.exists():
            old_json_path.replace(new_json_path)
        record.prompt_path = new_prompt_path
        record.response_path = new_response_path
        record.json_path = new_json_path
        record.status = status
        record.metadata["status"] = status

    def _max_status(self, left: TraceStatus, right: TraceStatus) -> TraceStatus:
        return left if self._status_rank[left] >= self._status_rank[right] else right

    def _build_stem(self, trace_id: str, stage: str, chunk_index: int | None) -> str:
        safe_stage = slugify(stage)
        if chunk_index is None:
            return f"{trace_id}-{safe_stage}"
        return f"{trace_id}-chunk-{chunk_index + 1:03d}-{safe_stage}"


def default_llm_trace_dir(output_file: str | Path) -> Path:
    run_log = os.getenv(RUN_LOG_ENV)
    if run_log:
        log_path = Path(run_log)
        if log_path.parent.exists():
            return log_path.with_name(f"{log_path.stem}_llm_trace")

    output_path = Path(output_file)
    return output_path.with_name(f"{output_path.stem}_llm_trace")


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def summarize_processed_translation(processed_translation: str | None, expected_count: int) -> ParseSummary | None:
    if expected_count <= 0:
        return None

    lines = [line.strip() for line in (processed_translation or "").splitlines() if line.strip()]
    translations: dict[int, str] = {}
    index_lines = 0
    for line_index, line in enumerate(lines):
        match = re.fullmatch(r"\[(\d+)\]", line)
        if not match:
            continue
        index_lines += 1
        local_index = int(match.group(1))
        if 1 <= local_index <= expected_count:
            translations[local_index] = lines[line_index + 1] if line_index + 1 < len(lines) else ""

    placeholder_count = sum(
        1
        for text in translations.values()
        if any(marker in text for marker in PLACEHOLDER_MARKERS)
    )
    parsed_count = sum(
        1
        for index in range(1, expected_count + 1)
        if translations.get(index, "").strip()
        and not any(marker in translations[index] for marker in PLACEHOLDER_MARKERS)
    )
    return ParseSummary(
        expected_count=expected_count,
        parsed_count=parsed_count,
        missing_count=max(0, expected_count - parsed_count),
        placeholder_count=placeholder_count,
        output_index_lines=index_lines,
    )


def status_from_parse(parse_summary: ParseSummary | None, *, error: str | None = None) -> TraceStatus:
    if error:
        return "failed"
    if parse_summary is None:
        return "ok"
    if parse_summary.expected_count > 0 and parse_summary.parsed_count == 0:
        return "failed"
    if parse_summary.missing_count >= parse_summary.expected_count and parse_summary.expected_count > 0:
        return "failed"
    if parse_summary.missing_count > 0 or parse_summary.placeholder_count > 0:
        return "suspicious"
    return "ok"


def status_from_quality(diagnosis: Any) -> TraceStatus | None:
    if diagnosis is None or not getattr(diagnosis, "has_issues", False):
        return "ok"
    return "suspicious"


def response_shape(response: str) -> dict[str, Any]:
    lines = response.splitlines()
    return {
        "line_count": len(lines),
        "char_count": len(response),
        "index_lines": sum(1 for line in lines if re.fullmatch(r"\s*\[\d+\]\s*", line)),
        "inline_index_markers": sum(1 for line in lines if re.match(r"\s*\[\d+\]\s+\S", line)),
        "xml_translation_block": bool(re.search(r"<translation\b", response, re.IGNORECASE)),
        "code_fence": "```" in response,
        "json_like": response.lstrip().startswith(("{", "[")),
        "markdown_table_like": any(line.strip().startswith("|") and line.strip().endswith("|") for line in lines),
    }


def chunk_summary(
    chunk: list[SubtitleEntry] | None,
    chunk_index: int | None,
    total_chunks: int | None,
) -> dict[str, Any] | None:
    if not chunk:
        return None
    return {
        "index": chunk_index + 1 if chunk_index is not None else None,
        "total": total_chunks,
        "entry_count": len(chunk),
        "first_entry_index": chunk[0].index,
        "last_entry_index": chunk[-1].index,
    }


def quality_summary(diagnosis: Any) -> dict[str, Any] | None:
    if diagnosis is None:
        return None
    return {
        "reliability": getattr(diagnosis, "reliability", None),
        "flagged_entries": getattr(diagnosis, "flagged_entries", None),
        "summary": getattr(diagnosis, "summary", None),
        "action_hint": getattr(diagnosis, "action_hint", None),
    }


def slugify(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower())
    return safe.strip("-") or "llm-call"
