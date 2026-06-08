from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from subtitle_llm.pipeline.quality import ChunkDiagnosis
from subtitle_llm.review import AutoReviewPort, ReviewPort

ReviewPolicyMode = Literal["auto", "tui"]


@dataclass(frozen=True)
class ReviewPolicy:
    mode: ReviewPolicyMode

    @classmethod
    def from_review_port(cls, review_port: ReviewPort) -> ReviewPolicy:
        if isinstance(review_port, AutoReviewPort):
            return cls(mode="auto")
        return cls(mode="tui")

    @property
    def uses_auto_repair(self) -> bool:
        return self.mode == "auto"

    @property
    def uses_manual_review(self) -> bool:
        return self.mode == "tui"

    def quality_visual_auto_repair(self, diagnosis: ChunkDiagnosis) -> bool:
        return diagnosis.has_issues and self.uses_auto_repair

    def should_auto_repair(self, diagnosis: ChunkDiagnosis) -> bool:
        return diagnosis.has_issues and self.uses_auto_repair

    def should_manual_review(self, diagnosis: ChunkDiagnosis) -> bool:
        return diagnosis.has_issues and self.uses_manual_review

    def post_repair_stage_status(self, diagnosis: ChunkDiagnosis) -> str:
        return "warning" if diagnosis.has_issues else "done"

    def post_repair_chunk_status(self, diagnosis: ChunkDiagnosis) -> str:
        return "warning" if diagnosis.has_issues else "done"
