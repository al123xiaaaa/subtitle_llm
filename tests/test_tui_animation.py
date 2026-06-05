import asyncio
import json
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

    def test_review_tui_merge_with_next_combines_original_and_translation(self):
        async def run_app() -> None:
            with tempfile.NamedTemporaryFile(suffix=".json") as temp_file:
                app = CustomHandlingApp(
                    [
                        SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "Hello", "你好", False),
                        SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "world", "世界", False),
                        SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "again", "又来了", False),
                    ],
                    temp_file.name,
                )
                async with app.run_test() as pilot:
                    table = app.query_one("#subtitles_table", DataTable)
                    table.move_cursor(row=0, animate=False)
                    await pilot.press("m")

                    self.assertEqual([entry.index for entry in app.subtitle_entries], [1, 3])
                    self.assertEqual(app.subtitle_entries[0].original_text, "Hello world")
                    self.assertEqual(app.subtitle_entries[0].translated_text, "你好 世界")
                    self.assertTrue(app.subtitle_entries[0].needs_retranslation)
                    self.assertEqual(
                        app.merge_map,
                        [{"merged_index": 1, "merged_from_indices": [1, 2]}],
                    )

                    await pilot.press("m")

                    self.assertEqual([entry.index for entry in app.subtitle_entries], [1])
                    self.assertEqual(app.subtitle_entries[0].original_text, "Hello world again")
                    self.assertEqual(app.subtitle_entries[0].translated_text, "你好 世界 又来了")
                    self.assertEqual(
                        app.merge_map,
                        [
                            {"merged_index": 1, "merged_from_indices": [1, 2]},
                            {"merged_index": 1, "merged_from_indices": [1, 3]},
                        ],
                    )

        asyncio.run(run_app())

    def test_review_tui_accept_all_after_merge_keeps_merged_translation(self):
        async def run_app(temp_path: str) -> None:
            app = CustomHandlingApp(
                [
                    SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "Hello", "你好", False),
                    SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "world", "世界", False),
                ],
                temp_path,
            )
            async with app.run_test() as pilot:
                table = app.query_one("#subtitles_table", DataTable)
                table.move_cursor(row=0, animate=False)
                await pilot.press("m")
                await pilot.press("y")

        with tempfile.TemporaryDirectory() as tmp:
            temp_path = str(Path(tmp) / "review.json")
            asyncio.run(run_app(temp_path))
            with open(temp_path, encoding="utf-8") as file:
                data = json.load(file)

        self.assertEqual(
            data["merge_map"],
            [{"merged_index": 1, "merged_from_indices": [1, 2]}],
        )
        self.assertEqual(len(data["selected_subtitle_entries"]), 1)
        merged_entry = data["selected_subtitle_entries"][0]
        self.assertEqual(merged_entry["original_text"], "Hello world")
        self.assertEqual(merged_entry["translated_text"], "你好 世界")
        self.assertFalse(merged_entry["needs_retranslation"])


if __name__ == "__main__":
    unittest.main()
