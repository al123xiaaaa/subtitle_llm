import sys
import unittest
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.io import SubtitleIO
from subtitle_llm.domain import Subtitle, SubtitleEntry


class TestFileHandler(unittest.TestCase):
    def test_read_srt_with_numeric_subtitle(self):
        # 创建一个包含数字字幕的 SRT 内容
        srt_content = """1
00:00:01,000 --> 00:00:04,000
This is the first subtitle

2
00:00:05,000 --> 00:00:08,000
42

3
00:00:09,000 --> 00:00:12,000
This is the third subtitle
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mock_file.srt"
            path.write_text(srt_content, encoding="utf-8")

            subtitle = SubtitleIO.read_srt(path)

            self.assertEqual(len(subtitle.entries), 3)
            self.assertEqual(subtitle.entries[0].text, "This is the first subtitle")
            self.assertEqual(subtitle.entries[1].text, "42")
            self.assertEqual(subtitle.entries[2].text, "This is the third subtitle")

    def test_write_srt_round_trips_multiline_text(self):
        subtitle = Subtitle(
            [
                SubtitleEntry(
                    index=1,
                    start_time="00:00:01,000",
                    end_time="00:00:04,000",
                    original_text="Hello world.",
                    translated_text="你好，世界。",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.srt"

            SubtitleIO.write_srt(subtitle, path, output_format="source-first")
            round_tripped = SubtitleIO.read_srt(path)

        self.assertEqual(len(round_tripped.entries), 1)
        self.assertEqual(round_tripped.entries[0].start_time, "00:00:01,000")
        self.assertEqual(round_tripped.entries[0].end_time, "00:00:04,000")
        self.assertEqual(round_tripped.entries[0].original_text, "Hello world.\n你好，世界。")


if __name__ == "__main__":
    unittest.main()
