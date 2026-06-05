from __future__ import annotations

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.review.ports import ReviewResult


class AutoReviewPort:
    def review(
        self,
        chunk: list[SubtitleEntry],
        chunk_index: int,
        total_chunks: int,
        completed_chunks: int = 0,
    ) -> ReviewResult:
        return ReviewResult(
            chunk=chunk,
            entries_to_retranslate=[entry for entry in chunk if entry.needs_retranslation],
        )
