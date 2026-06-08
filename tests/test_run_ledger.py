import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.run_ledger import RunLedger


class RunLedgerTest(unittest.TestCase):
    def test_finalize_subtitle_applies_removed_rows_and_fallback_text(self):
        subtitle = Subtitle([
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "First"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "Second"),
            SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "Third"),
        ])
        translated_entries = [
            SubtitleEntry(
                1,
                "00:00:00,000",
                "00:00:02,000",
                "First Second",
                "第一第二",
            )
        ]
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            checkpoint_file="checkpoint.json",
            context_file="context.txt",
        )
        ledger = RunLedger(removed_entry_indices={2})

        ledger.finalize_subtitle(subtitle, translated_entries, report)

        self.assertEqual([entry.index for entry in subtitle.entries], [1, 2])
        self.assertEqual(subtitle.entries[0].original_text, "First Second")
        self.assertEqual(subtitle.entries[0].translated_text, "第一第二")
        self.assertEqual(subtitle.entries[1].original_text, "Third")
        self.assertEqual(subtitle.entries[1].translated_text, "Third")
        self.assertEqual(report.stage, "完成")
        self.assertEqual(report.removed_entry_indices, [2])
        self.assertEqual(report.final_output_entries, 2)


if __name__ == "__main__":
    unittest.main()
