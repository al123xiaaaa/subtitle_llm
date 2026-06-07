import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.pipeline.normalization import (
    NormalizationOptions,
    normalize_subtitle,
    split_sentences,
    srt_time_to_ms,
    write_normalization_map,
)


def make_entry(index: int, start: str, end: str, text: str) -> SubtitleEntry:
    return SubtitleEntry(index=index, start_time=start, end_time=end, original_text=text)


class TestSubtitleNormalization(unittest.TestCase):
    def test_splits_punctuation_before_lowercase_as_sentence_boundary(self):
        spans = split_sentences("this is one sentence. another starts lowercase. Version 3.14 stays together.")

        self.assertEqual([span.text for span in spans], [
            "this is one sentence.",
            "another starts lowercase.",
            "Version 3.14 stays together.",
        ])

    def test_pysbd_handles_english_abbreviation_boundaries(self):
        spans = split_sentences("No. 5 is ready. Continue.")

        self.assertEqual([span.text for span in spans], ["No. 5 is ready.", "Continue."])

    def test_normalizes_rolling_caption_into_sentence_cues(self):
        subtitle = Subtitle(
            [
                make_entry(1, "00:00:00,000", "00:00:04,760", "A few months ago, I wrote a few"),
                make_entry(2, "00:00:02,240", "00:00:06,120", "sentences, about four sentences, that"),
                make_entry(3, "00:00:04,760", "00:00:09,040", "have turned out to be the most"),
                make_entry(4, "00:00:06,120", "00:00:10,760", "influential four sentences I've ever"),
                make_entry(5, "00:00:09,040", "00:00:13,120", "written. I packaged these four sentences"),
                make_entry(6, "00:00:10,760", "00:00:15,800", "up into the Grill Me skill, which is a"),
                make_entry(7, "00:00:13,120", "00:00:17,560", "skill that you can use to get the LLM to"),
                make_entry(8, "00:00:15,800", "00:00:19,560", "interview you relentlessly."),
            ]
        )

        result = normalize_subtitle(
            subtitle,
            NormalizationOptions(max_cue_chars=220, max_duration_seconds=30),
        )

        self.assertTrue(result.applied)
        self.assertEqual(result.stats.original_entries, 8)
        self.assertEqual(len(result.subtitle.entries), 2)
        self.assertIn("A few months ago", result.subtitle.entries[0].original_text)
        self.assertIn("written.", result.subtitle.entries[0].original_text)
        self.assertIn("I packaged", result.subtitle.entries[1].original_text)
        self.assertEqual(result.cue_map[0].original_start_index, 1)
        self.assertEqual(result.cue_map[0].original_end_index, 5)
        self.assertGreaterEqual(result.stats.overlap_ratio, 0.9)
        for previous, current in zip(result.subtitle.entries, result.subtitle.entries[1:]):
            self.assertLess(srt_time_to_ms(previous.end_time), srt_time_to_ms(current.start_time))
        for entry in result.subtitle.entries:
            duration_ms = srt_time_to_ms(entry.end_time) - srt_time_to_ms(entry.start_time)
            self.assertGreaterEqual(duration_ms, 800)

    def test_skips_normal_subtitle_in_auto_mode(self):
        subtitle = Subtitle(
            [
                make_entry(1, "00:00:00,000", "00:00:02,000", "Hello world."),
                make_entry(2, "00:00:02,500", "00:00:04,000", "This is already readable."),
            ]
        )

        result = normalize_subtitle(subtitle)

        self.assertFalse(result.applied)
        self.assertEqual(result.subtitle, subtitle)
        self.assertIn("does not look like rolling captions", result.reason)

    def test_writes_normalization_map(self):
        subtitle = Subtitle(
            [
                make_entry(1, "00:00:00,000", "00:00:04,000", "This is a"),
                make_entry(2, "00:00:02,000", "00:00:06,000", "rolling caption."),
                make_entry(3, "00:00:04,000", "00:00:08,000", "Another sentence."),
            ]
        )
        result = normalize_subtitle(subtitle, NormalizationOptions(mode="always", max_cue_chars=120))

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "map.json"
            write_normalization_map(result, output_path)
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertTrue(payload["applied"])
        self.assertEqual(payload["stats"]["original_entries"], 3)
        self.assertEqual(payload["cues"][0]["original_start_index"], 1)


if __name__ == "__main__":
    unittest.main()
