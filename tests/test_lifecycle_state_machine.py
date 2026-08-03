import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.pipeline.lifecycle import (
    LifecycleTransitionError,
    TranslationChunkLifecycle,
    TranslationChunkLifecycleEvent,
    TranslationChunkState,
    TranslationTaskLifecycle,
    TranslationTaskLifecycleEvent,
    TranslationTaskState,
)


class TestTranslationTaskLifecycle(unittest.TestCase):
    def test_task_moves_through_guarded_lifecycle(self):
        lifecycle = TranslationTaskLifecycle()

        lifecycle = lifecycle.apply(TranslationTaskLifecycleEvent.PREPARE_INPUT_STARTED)
        self.assertEqual(lifecycle.state, TranslationTaskState.PREPARING_INPUT)

        lifecycle = lifecycle.apply(TranslationTaskLifecycleEvent.PREPARE_INPUT_COMPLETED)
        self.assertEqual(lifecycle.state, TranslationTaskState.PREPARING_TRANSLATION)

        lifecycle = lifecycle.apply(TranslationTaskLifecycleEvent.PREPARE_TRANSLATION_COMPLETED)
        self.assertEqual(lifecycle.state, TranslationTaskState.PROCESSING_CHUNKS)

        lifecycle = lifecycle.apply(TranslationTaskLifecycleEvent.PROCESS_CHUNKS_COMPLETED)
        self.assertEqual(lifecycle.state, TranslationTaskState.FINALIZING_OUTPUT)

        lifecycle = lifecycle.apply(TranslationTaskLifecycleEvent.FINALIZE_OUTPUT_COMPLETED_WITH_WARNINGS)
        self.assertEqual(lifecycle.state, TranslationTaskState.COMPLETED_WITH_WARNINGS)

    def test_task_rejects_out_of_order_transition(self):
        lifecycle = TranslationTaskLifecycle()

        with self.assertRaises(LifecycleTransitionError):
            lifecycle.apply(TranslationTaskLifecycleEvent.FINALIZE_OUTPUT_STARTED)

    def test_failed_task_can_be_reopened_for_resume(self):
        lifecycle = TranslationTaskLifecycle.from_state(TranslationTaskState.FAILED)

        lifecycle = lifecycle.apply(TranslationTaskLifecycleEvent.PREPARE_TRANSLATION_STARTED)

        self.assertEqual(lifecycle.state, TranslationTaskState.PREPARING_TRANSLATION)


class TestTranslationChunkLifecycle(unittest.TestCase):
    def test_chunk_success_path(self):
        lifecycle = TranslationChunkLifecycle()

        lifecycle = lifecycle.apply(TranslationChunkLifecycleEvent.TRANSLATION_STARTED)
        self.assertEqual(lifecycle.state, TranslationChunkState.TRANSLATING)

        lifecycle = lifecycle.apply(TranslationChunkLifecycleEvent.TRANSLATION_COMPLETED)
        self.assertEqual(lifecycle.state, TranslationChunkState.CHECKING_QUALITY)

        lifecycle = lifecycle.apply(TranslationChunkLifecycleEvent.ACCEPTED)
        self.assertEqual(lifecycle.state, TranslationChunkState.ACCEPTED)

    def test_chunk_repair_returns_to_quality_check(self):
        lifecycle = TranslationChunkLifecycle()
        lifecycle = lifecycle.apply(TranslationChunkLifecycleEvent.TRANSLATION_STARTED)
        lifecycle = lifecycle.apply(TranslationChunkLifecycleEvent.TRANSLATION_COMPLETED)
        lifecycle = lifecycle.apply(TranslationChunkLifecycleEvent.REPAIR_REQUIRED)
        self.assertEqual(lifecycle.state, TranslationChunkState.REPAIRING)

        lifecycle = lifecycle.apply(TranslationChunkLifecycleEvent.REPAIR_COMPLETED)

        self.assertEqual(lifecycle.state, TranslationChunkState.CHECKING_QUALITY)

    def test_chunk_terminal_state_rejects_more_events(self):
        lifecycle = TranslationChunkLifecycle()
        lifecycle = lifecycle.apply(TranslationChunkLifecycleEvent.TRANSLATION_STARTED)
        lifecycle = lifecycle.apply(TranslationChunkLifecycleEvent.TRANSLATION_COMPLETED)
        lifecycle = lifecycle.apply(TranslationChunkLifecycleEvent.ACCEPTED_WITH_WARNINGS)

        with self.assertRaises(LifecycleTransitionError):
            lifecycle.apply(TranslationChunkLifecycleEvent.REPAIR_REQUIRED)


if __name__ == "__main__":
    unittest.main()
