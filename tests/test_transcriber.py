import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.media.transcriber import _resolve_model_path, normalize_asr_language


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


if __name__ == "__main__":
    unittest.main()
