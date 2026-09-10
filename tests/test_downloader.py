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
    info = {
        "title": "Demo Video",
        "subtitles": {},
        "automatic_captions": {},
    }

    def __init__(self, options):
        self.options = options
        self.downloaded_urls = []
        FakeYoutubeDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def extract_info(self, url, download=False):
        return self.info

    def download(self, urls):
        self.downloaded_urls.extend(urls)
        output_path = Path(self.options["outtmpl"]).parent
        (output_path / "Demo Video.webm").write_text("video", encoding="utf-8")
        (output_path / "Demo Video.wav").write_text("audio", encoding="utf-8")


class TestDownloader(unittest.TestCase):
    def setUp(self):
        FakeYoutubeDL.instances = []
        FakeYoutubeDL.info = {
            "title": "Demo Video",
            "subtitles": {},
            "automatic_captions": {},
        }

    def test_keeps_downloaded_video_when_extracting_audio_for_asr(self):
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

    def test_reuses_existing_subtitle_without_downloading_video(self):
        FakeYoutubeDL.info = {
            "title": "Demo Video",
            "subtitles": {"en": [{}]},
            "automatic_captions": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp)
            (output_path / "Demo Video.webm").write_text("video", encoding="utf-8")
            (output_path / "Demo Video.en.srt").write_text("subtitle", encoding="utf-8")

            with patch("subtitle_llm.media.downloader.YoutubeDL", FakeYoutubeDL):
                result = downloader.download("https://example.test/video", tmp, "en")

        self.assertEqual(len(FakeYoutubeDL.instances), 1)
        self.assertEqual(FakeYoutubeDL.instances[0].downloaded_urls, [])
        video_path, subtitle_path = cast(tuple[str | None, str | None], result)
        self.assertTrue(str(video_path).endswith("Demo Video.webm"))
        self.assertTrue(str(subtitle_path).endswith("Demo Video.en.srt"))

    def test_force_asr_ignores_available_and_cached_subtitles(self):
        FakeYoutubeDL.info = {
            "title": "Demo Video",
            "subtitles": {"en": [{}]},
            "automatic_captions": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp)
            (output_path / "Demo Video.webm").write_text("video", encoding="utf-8")
            (output_path / "Demo Video.en.srt").write_text("subtitle", encoding="utf-8")

            with patch("subtitle_llm.media.downloader.YoutubeDL", FakeYoutubeDL):
                result = downloader.download("https://example.test/video", tmp, "en", force_asr=True)

        self.assertEqual(len(FakeYoutubeDL.instances), 2)
        download_options = FakeYoutubeDL.instances[1].options
        self.assertNotIn("writesubtitles", download_options)
        self.assertEqual(download_options["postprocessors"][0]["key"], "FFmpegExtractAudio")
        video_path, subtitle_path, audio_path = cast(tuple[str | None, None, str | None], result)
        self.assertTrue(str(video_path).endswith("Demo Video.webm"))
        self.assertIsNone(subtitle_path)
        self.assertTrue(str(audio_path).endswith("Demo Video.wav"))

    def test_reuses_existing_audio_without_downloading_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp)
            (output_path / "Demo Video.webm").write_text("video", encoding="utf-8")
            (output_path / "Demo Video.wav").write_text("audio", encoding="utf-8")

            with patch("subtitle_llm.media.downloader.YoutubeDL", FakeYoutubeDL):
                result = downloader.download("https://example.test/video", tmp, "en")

        self.assertEqual(len(FakeYoutubeDL.instances), 1)
        self.assertEqual(FakeYoutubeDL.instances[0].downloaded_urls, [])
        video_path, subtitle_path, audio_path = cast(tuple[str | None, None, str | None], result)
        self.assertTrue(str(video_path).endswith("Demo Video.webm"))
        self.assertIsNone(subtitle_path)
        self.assertTrue(str(audio_path).endswith("Demo Video.wav"))

    def test_downloads_when_only_video_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Demo Video.webm").write_text("video", encoding="utf-8")

            with patch("subtitle_llm.media.downloader.YoutubeDL", FakeYoutubeDL):
                result = downloader.download("https://example.test/video", tmp, "en")

        self.assertEqual(len(FakeYoutubeDL.instances), 2)
        self.assertEqual(FakeYoutubeDL.instances[1].downloaded_urls, ["https://example.test/video"])
        video_path, subtitle_path, audio_path = cast(tuple[str | None, None, str | None], result)
        self.assertTrue(str(video_path).endswith("Demo Video.webm"))
        self.assertIsNone(subtitle_path)
        self.assertTrue(str(audio_path).endswith("Demo Video.wav"))

    def test_subtitle_download_passes_ffmpeg_location_when_available(self):
        FakeYoutubeDL.info = {
            "title": "Demo Video",
            "subtitles": {"en": [{}]},
            "automatic_captions": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("subtitle_llm.media.downloader.YoutubeDL", FakeYoutubeDL),
                patch(
                    "subtitle_llm.media.downloader._resolve_ffmpeg_location",
                    return_value="/opt/homebrew/bin/ffmpeg",
                ),
            ):
                downloader.download("https://example.test/video", tmp, "en")

        download_options = FakeYoutubeDL.instances[1].options
        self.assertEqual(download_options["ffmpeg_location"], "/opt/homebrew/bin/ffmpeg")
        self.assertEqual(download_options["format"], "bestvideo+bestaudio/best")

    def test_subtitle_download_falls_back_to_single_file_format_without_ffmpeg(self):
        FakeYoutubeDL.info = {
            "title": "Demo Video",
            "subtitles": {"en": [{}]},
            "automatic_captions": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("subtitle_llm.media.downloader.YoutubeDL", FakeYoutubeDL),
                patch("subtitle_llm.media.downloader._resolve_ffmpeg_location", return_value=None),
            ):
                downloader.download("https://example.test/video", tmp, "en")

        download_options = FakeYoutubeDL.instances[1].options
        self.assertNotIn("ffmpeg_location", download_options)
        self.assertEqual(download_options["format"], "best[ext=mp4]/best")

    def test_cached_subtitle_with_missing_video_triggers_video_only_download(self):
        FakeYoutubeDL.info = {
            "title": "Demo Video",
            "subtitles": {"en": [{}]},
            "automatic_captions": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp)
            (output_path / "Demo Video.en.srt").write_text("subtitle", encoding="utf-8")

            with (
                patch("subtitle_llm.media.downloader.YoutubeDL", FakeYoutubeDL),
                patch(
                    "subtitle_llm.media.downloader._resolve_ffmpeg_location",
                    return_value="/opt/homebrew/bin/ffmpeg",
                ),
            ):
                result = downloader.download("https://example.test/video", tmp, "en")

        self.assertEqual(len(FakeYoutubeDL.instances), 2)
        download_options = FakeYoutubeDL.instances[1].options
        self.assertEqual(FakeYoutubeDL.instances[1].downloaded_urls, ["https://example.test/video"])
        self.assertNotIn("writesubtitles", download_options)
        self.assertEqual(download_options["ffmpeg_location"], "/opt/homebrew/bin/ffmpeg")
        video_path, subtitle_path = cast(tuple[str | None, str | None], result)
        self.assertTrue(str(video_path).endswith("Demo Video.webm"))
        self.assertTrue(str(subtitle_path).endswith("Demo Video.en.srt"))


class BotWallYoutubeDL:
    """第一次探测抛反机器人墙，带 cookies 后放行。"""

    instances = []
    bot_wall = True

    def __init__(self, options):
        self.options = options
        BotWallYoutubeDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def extract_info(self, url, download=False):
        if "cookiesfrombrowser" not in self.options and BotWallYoutubeDL.bot_wall:
            raise RuntimeError("ERROR: [youtube] abc: Sign in to confirm you're not a bot")
        return {"title": "Demo Video", "subtitles": {}, "automatic_captions": {}}

    def download(self, urls):
        output_path = Path(self.options["outtmpl"]).parent
        (output_path / "Demo Video.webm").write_text("video", encoding="utf-8")
        (output_path / "Demo Video.wav").write_text("audio", encoding="utf-8")


class TestCookieFallback(unittest.TestCase):
    def setUp(self):
        BotWallYoutubeDL.instances = []
        BotWallYoutubeDL.bot_wall = True

    def test_bot_wall_retries_with_browser_cookies(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("subtitle_llm.media.downloader.YoutubeDL", BotWallYoutubeDL):
                downloader.download("https://example.test/video", tmp, "en")

        probe = BotWallYoutubeDL.instances[0]
        self.assertNotIn("cookiesfrombrowser", probe.options)
        retried = BotWallYoutubeDL.instances[1]
        self.assertEqual(retried.options["cookiesfrombrowser"], ("chrome",))
        # 后续下载沿用通过验证的同一浏览器
        download_options = BotWallYoutubeDL.instances[-1].options
        self.assertEqual(download_options.get("cookiesfrombrowser"), ("chrome",))

    def test_bot_wall_without_usable_cookies_raises_friendly_error(self):
        class AlwaysWalled(BotWallYoutubeDL):
            def extract_info(self, url, download=False):
                raise RuntimeError("ERROR: [youtube] abc: Sign in to confirm you're not a bot")

        with tempfile.TemporaryDirectory() as tmp:
            with patch("subtitle_llm.media.downloader.YoutubeDL", AlwaysWalled):
                with self.assertRaisesRegex(RuntimeError, "反机器人"):
                    downloader.download("https://example.test/video", tmp, "en")

    def test_env_specified_browser_is_used_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("subtitle_llm.media.downloader.YoutubeDL", BotWallYoutubeDL),
                patch.dict("os.environ", {"SUBTITLE_LLM_COOKIES_BROWSER": "firefox"}),
            ):
                downloader.download("https://example.test/video", tmp, "en")

        probe = BotWallYoutubeDL.instances[0]
        self.assertEqual(probe.options["cookiesfrombrowser"], ("firefox",))


if __name__ == "__main__":
    unittest.main()
