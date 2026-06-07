from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from subtitle_llm.domain import SubtitleEntry


@dataclass
class ReviewResult:
    chunk: list[SubtitleEntry]
    entries_to_retranslate: list[SubtitleEntry]
    alignment_drift_start_index: int | None = None
    cascade_start_index: int | None = None
    removed_entry_indices: list[int] = field(default_factory=list)


class ReviewPort(Protocol):
    def review(
        self,
        chunk: list[SubtitleEntry],
        chunk_index: int,
        total_chunks: int,
        completed_chunks: int = 0,
    ) -> ReviewResult:
        ...
