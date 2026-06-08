import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.pipeline.quality import ChunkDiagnosis, IssueGroup
from subtitle_llm.pipeline.review_policy import ReviewPolicy
from subtitle_llm.review import AutoReviewPort


class ManualReviewPort:
    def review(self, chunk, chunk_index, total_chunks, completed_chunks=0):
        raise NotImplementedError


def diagnosis_with_issue() -> ChunkDiagnosis:
    return ChunkDiagnosis(
        reliability="fail",
        summary="missing translation",
        issue_groups=[
            IssueGroup(
                issue_type="missing_translation",
                severity="high",
                affected_indices=[1],
                description="entry 1 is missing",
            )
        ],
        action_hint="repair",
        total_entries=1,
        flagged_entries=1,
    )


def clean_diagnosis() -> ChunkDiagnosis:
    return ChunkDiagnosis(
        reliability="ok",
        summary="",
        issue_groups=[],
        action_hint="accept",
        total_entries=1,
        flagged_entries=0,
    )


class TestReviewPolicy(unittest.TestCase):
    def test_auto_review_port_uses_auto_repair(self):
        policy = ReviewPolicy.from_review_port(AutoReviewPort())
        diagnosis = diagnosis_with_issue()

        self.assertTrue(policy.uses_auto_repair)
        self.assertTrue(policy.should_auto_repair(diagnosis))
        self.assertFalse(policy.should_manual_review(diagnosis))
        self.assertEqual(policy.post_repair_stage_status(diagnosis), "warning")

    def test_manual_review_port_uses_manual_review(self):
        policy = ReviewPolicy.from_review_port(ManualReviewPort())
        diagnosis = diagnosis_with_issue()

        self.assertFalse(policy.uses_auto_repair)
        self.assertFalse(policy.should_auto_repair(diagnosis))
        self.assertTrue(policy.should_manual_review(diagnosis))

    def test_clean_diagnosis_needs_no_review_action(self):
        policy = ReviewPolicy.from_review_port(AutoReviewPort())
        diagnosis = clean_diagnosis()

        self.assertFalse(policy.should_auto_repair(diagnosis))
        self.assertFalse(policy.should_manual_review(diagnosis))
        self.assertEqual(policy.post_repair_chunk_status(diagnosis), "done")


if __name__ == "__main__":
    unittest.main()
