import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.media import downloader


class FakeYoutubeDL:
    instances = []

    def __init__(self, options):
        self.options = options
        self.downloaded_urls = []
        FakeYoutubeDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def extract_info(self, url, download=False):
        return {
            "title": "Demo Video",
            "subtitles": {},
            "automatic_captions": {},
        }

    def download(self, urls):
        self.downloaded_urls.extend(urls)
        output_path = Path(self.options["outtmpl"]).parent
        (output_path / "Demo Video.webm").write_text("video", encoding="utf-8")
        (output_path / "Demo Video.wav").write_text("audio", encoding="utf-8")


class TestDownloader(unittest.TestCase):
    def test_keeps_downloaded_video_when_extracting_audio_for_asr(self):
        FakeYoutubeDL.instances = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch("subtitle_llm.media.downloader.YoutubeDL", FakeYoutubeDL):
                result = downloader.download("https://example.test/video", tmp, "en")

        download_options = FakeYoutubeDL.instances[1].options
        self.assertEqual(len(result), 3)
        video_path, subtitle_path, audio_path = cast(tuple[str | None, None, str | None], result)
        self.assertTrue(download_options["keepvideo"])
        self.assertEqual(download_options["postprocessors"][0]["key"], "FFmpegExtractAudio")
        self.assertIsNone(subtitle_path)
        self.assertTrue(str(video_path).endswith("Demo Video.webm"))
        self.assertTrue(str(audio_path).endswith("Demo Video.wav"))


if __name__ == "__main__":
    unittest.main()
