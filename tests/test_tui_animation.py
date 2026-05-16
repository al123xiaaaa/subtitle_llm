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
from textual.widgets import ProgressBar, Static


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
                    chunk_index=1,
                    total_chunks=4,
                )
                async with app.run_test():
                    self.assertIsNotNone(app.query_one("#activity"))
                    self.assertIsNotNone(app.query_one("#status"))
                    progress_label = app.query_one("#progress_label", Static)
                    chunk_progress = app.query_one("#chunk_progress", ProgressBar)
                    self.assertEqual(str(progress_label.renderable), "Chunk 2/4")
                    self.assertEqual(chunk_progress.progress, 2)
                    self.assertEqual(chunk_progress.total, 4)
                    self.assertIsNotNone(app.query_one("#subtitles_table"))

        asyncio.run(run_app())


if __name__ == "__main__":
    unittest.main()
