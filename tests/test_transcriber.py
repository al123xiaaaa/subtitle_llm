import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.media.transcriber import _resolve_model_path, _time_stamps_to_subtitle_entries, normalize_asr_language
from subtitle_llm.settings import ASRConfig


class TestTranscriberLanguage(unittest.TestCase):
    def test_normalizes_url_language_codes_for_qwen_asr(self):
        self.assertEqual(normalize_asr_language("en"), "English")
        self.assertEqual(normalize_asr_language("en-US"), "English")
        self.assertEqual(normalize_asr_language("zh"), "Chinese")
        self.assertEqual(normalize_asr_language("zh-HK"), "Cantonese")

    def test_preserves_supported_language_names(self):
        self.assertEqual(normalize_asr_language("English"), "English")
        self.assertEqual(normalize_asr_language(" chinese "), "Chinese")

    def test_auto_language_detection_aliases(self):
        self.assertIsNone(normalize_asr_language("auto"))
        self.assertIsNone(normalize_asr_language(""))

    def test_unknown_language_uses_original_value_for_qwen_error(self):
        self.assertEqual(normalize_asr_language("Klingon"), "Klingon")

    def test_resolve_model_path_keeps_local_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_resolve_model_path(tmp, None, True), tmp)

    def test_resolve_model_path_prefers_cached_snapshot(self):
        with patch("subtitle_llm.media.transcriber._download_model_snapshot", return_value="/cached/snapshot") as download:
            self.assertEqual(_resolve_model_path("Qwen/Qwen3-ASR-1.7B", None, True), "/cached/snapshot")

        download.assert_called_once_with("Qwen/Qwen3-ASR-1.7B", None, local_files_only=True)

    def test_resolve_model_path_downloads_after_cache_miss(self):
        with patch(
            "subtitle_llm.media.transcriber._download_model_snapshot",
            side_effect=[FileNotFoundError("not cached"), "/downloaded/snapshot"],
        ) as download:
            self.assertEqual(_resolve_model_path("Qwen/Qwen3-ASR-1.7B", "/tmp/hf-cache", True), "/downloaded/snapshot")

        self.assertEqual(download.call_args_list[0].kwargs["local_files_only"], True)
        self.assertEqual(download.call_args_list[1].kwargs["local_files_only"], False)

    def test_groups_word_level_asr_timestamps_into_readable_subtitles(self):
        stamps = [
            SimpleNamespace(text="He", start_time=0.0, end_time=0.3),
            SimpleNamespace(text="handles", start_time=0.3, end_time=0.6),
            SimpleNamespace(text="it", start_time=0.6, end_time=1.0),
            SimpleNamespace(text="Fox", start_time=1.6, end_time=1.9),
            SimpleNamespace(text="knocks", start_time=1.9, end_time=2.2),
            SimpleNamespace(text="down", start_time=2.2, end_time=2.4),
            SimpleNamespace(text="a", start_time=2.4, end_time=2.5),
            SimpleNamespace(text="three", start_time=2.5, end_time=2.9),
        ]

        entries = _time_stamps_to_subtitle_entries(stamps, 0.0, 1, "English", ASRConfig())

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].original_text, "He handles it")
        self.assertEqual(entries[1].original_text, "Fox knocks down a three")
        self.assertEqual(entries[0].start_time, "00:00:00,000")
        self.assertEqual(entries[1].start_time, "00:00:01,600")

    def test_uses_asr_reference_text_punctuation_for_sentence_boundaries(self):
        stamps = [
            SimpleNamespace(text="He", start_time=0.0, end_time=0.3),
            SimpleNamespace(text="handles", start_time=0.3, end_time=0.6),
            SimpleNamespace(text="it", start_time=0.6, end_time=1.0),
            SimpleNamespace(text="Fox", start_time=1.1, end_time=1.4),
            SimpleNamespace(text="scores", start_time=1.4, end_time=1.8),
        ]

        entries = _time_stamps_to_subtitle_entries(
            stamps,
            0.0,
            1,
            "English",
            ASRConfig(subtitle_gap_seconds=10.0),
            reference_text="He handles it. Fox scores.",
        )

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].original_text, "He handles it.")
        self.assertEqual(entries[1].original_text, "Fox scores.")

    def test_groups_cjk_asr_timestamps_without_spaces(self):
        stamps = [
            SimpleNamespace(text="你", start_time=0.0, end_time=0.2),
            SimpleNamespace(text="好", start_time=0.2, end_time=0.4),
            SimpleNamespace(text="世界", start_time=0.4, end_time=0.8),
        ]

        entries = _time_stamps_to_subtitle_entries(stamps, 0.0, 1, "Chinese", ASRConfig())

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].original_text, "你好世界")


if __name__ == "__main__":
    unittest.main()
