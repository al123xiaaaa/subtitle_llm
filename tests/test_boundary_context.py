import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.pipeline.prompts import TRANSLATE_CHUNK_PROMPT
from subtitle_llm.pipeline.text import (
    build_boundary_context,
    detect_boundary_risk,
    is_likely_continuation,
    is_sentence_complete,
)


def make_entry(index, text):
    return SubtitleEntry(
        index=index,
        start_time="00:00:00,000",
        end_time="00:00:01,000",
        original_text=text,
    )


class TestBoundaryContext(unittest.TestCase):
    def test_sentence_complete_detection(self):
        self.assertTrue(is_sentence_complete("This is done."))
        self.assertTrue(is_sentence_complete("完成了。"))
        self.assertTrue(is_sentence_complete("Really?"))
        self.assertTrue(is_sentence_complete("done.”"))
        self.assertTrue(is_sentence_complete("完成了。”"))
        self.assertFalse(is_sentence_complete("because it was"))
        self.assertFalse(is_sentence_complete("我们接下来要"))

    def test_likely_continuation_detection(self):
        self.assertTrue(is_likely_continuation("and then we continue"))
        self.assertTrue(is_likely_continuation("which means"))
        self.assertTrue(is_likely_continuation("because of this"))
        self.assertFalse(is_likely_continuation("This starts a new sentence"))

    def test_boundary_risk_detection(self):
        self.assertIsNotNone(
            detect_boundary_risk(make_entry(1, "This continues"), make_entry(2, "into the next line"))
        )
        self.assertIsNotNone(
            detect_boundary_risk(make_entry(1, "This is done."), make_entry(2, "and then continues"))
        )
        self.assertIsNone(
            detect_boundary_risk(make_entry(1, "This is done."), make_entry(2, "This starts fresh."))
        )

    def test_boundary_context_uses_previous_and_next_window(self):
        entries = [make_entry(i, f"Entry {i}.") for i in range(1, 12)]
        context = build_boundary_context(entries, entries[4:6], window_size=4)

        self.assertIn("[global 1]", context["text"])
        self.assertIn("[global 4]", context["text"])
        self.assertIn("[global 7]", context["text"])
        self.assertIn("[global 10]", context["text"])
        self.assertNotIn("[global 11]", context["text"])

    def test_boundary_context_handles_edges(self):
        entries = [make_entry(i, f"Entry {i}.") for i in range(1, 5)]
        first_context = build_boundary_context(entries, entries[:1], window_size=4)
        last_context = build_boundary_context(entries, entries[-1:], window_size=4)

        self.assertIn("Previous context:\n(none)", first_context["text"])
        self.assertIn("Next context:\n(none)", last_context["text"])

    def test_prompt_keeps_chunk_size_independent_from_context(self):
        entries = [make_entry(i, f"Entry {i}") for i in range(1, 7)]
        chunk = entries[2:4]
        boundary_context = build_boundary_context(entries, chunk, window_size=2)["text"]
        prompt = TRANSLATE_CHUNK_PROMPT.format(
            target_language="Chinese",
            context="Overall summary: test",
            boundary_context=boundary_context,
            chunk_text="[1]\n[Entry 3]\n[2]\n[Entry 4]",
            chunk_size=len(chunk),
        )

        self.assertIn("Readonly Boundary Context", prompt)
        self.assertIn("Ensure your translation contains exactly 2 entries", prompt)
        self.assertIn("output readonly boundary context entries", prompt)


if __name__ == "__main__":
    unittest.main()
