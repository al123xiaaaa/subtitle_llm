from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from subtitle_llm.domain import SubtitleEntry


@dataclass
class ReviewResult:
    chunk: list[SubtitleEntry]
    entries_to_retranslate: list[SubtitleEntry]


class ReviewPort(Protocol):
    def review(
        self,
        chunk: list[SubtitleEntry],
        chunk_index: int,
        total_chunks: int,
        completed_chunks: int = 0,
    ) -> ReviewResult:
        ...
