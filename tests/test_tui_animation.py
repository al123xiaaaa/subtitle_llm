import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.review.custom_handling import CustomHandlingApp
from textual.widgets import DataTable, ProgressBar, Static


class TestTuiAnimation(unittest.TestCase):
    def test_review_tui_mounts_activity_indicator(self):
        async def run_app() -> None:
            with tempfile.NamedTemporaryFile(suffix=".json") as temp_file:
                app = CustomHandlingApp(
                    [
                        SubtitleEntry(
                            1,
                            "00:00:00,000",
                            "00:00:01,000",
                            "Hello",
                            "你好",
                            True,
                        )
                    ],
                    temp_file.name,
                    chunk_index=3,
                    total_chunks=4,
                    completed_chunks=0,
                )
                async with app.run_test():
                    self.assertIsNotNone(app.query_one("#activity"))
                    self.assertIsNotNone(app.query_one("#status"))
                    progress_label = app.query_one("#progress_label", Static)
                    chunk_progress = app.query_one("#chunk_progress", ProgressBar)
                    self.assertEqual(str(progress_label.renderable), "进度 1/4 | 当前片段 4/4 | 待处理 1")
                    self.assertEqual(chunk_progress.progress, 1)
                    self.assertEqual(chunk_progress.total, 4)
                    self.assertIsNotNone(app.query_one("#subtitles_table"))
                    self.assertIsNotNone(app.query_one("#current_detail"))

        asyncio.run(run_app())

    def test_review_tui_defaults_to_cascade_from_first_issue(self):
        async def run_app() -> None:
            with tempfile.NamedTemporaryFile(suffix=".json") as temp_file:
                app = CustomHandlingApp(
                    [
                        SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "First.", "第一句", False),
                        SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "Second.", "", True),
                        SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "Third.", "第三句", False),
                    ],
                    temp_file.name,
                )
                async with app.run_test():
                    self.assertFalse(app.subtitle_entries[0].needs_retranslation)
                    self.assertTrue(app.subtitle_entries[1].needs_retranslation)
                    self.assertTrue(app.subtitle_entries[2].needs_retranslation)
                    progress_label = app.query_one("#progress_label", Static)
                    self.assertIn("待处理 2", str(progress_label.renderable))

        asyncio.run(run_app())

    def test_review_tui_marks_alignment_drift_start(self):
        async def run_app() -> None:
            with tempfile.NamedTemporaryFile(suffix=".json") as temp_file:
                app = CustomHandlingApp(
                    [
                        SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "First.", "第一句", False),
                        SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "Second.", "", True),
                        SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "Third.", "第三句", False),
                    ],
                    temp_file.name,
                )
                async with app.run_test() as pilot:
                    table = app.query_one("#subtitles_table", DataTable)
                    table.move_cursor(row=1, animate=False)
                    await pilot.press("d")
                    self.assertEqual(app.alignment_drift_start_index(), 2)
                    self.assertEqual([entry.index for entry in app.selected_entries()], [2, 3])

        asyncio.run(run_app())


if __name__ == "__main__":
    unittest.main()
