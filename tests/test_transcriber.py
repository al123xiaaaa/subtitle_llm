import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.media.transcriber import (
    _clean_funasr_text,
    _to_funasr_language,
    normalize_asr_language,
    transcribe,
)
from subtitle_llm.settings import ASRConfig


class TestTranscriberLanguage(unittest.TestCase):
    """语言标准化和 FunASR 语言代码映射测试。"""

    def test_normalizes_url_language_codes(self):
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

    def test_unknown_language_uses_original_value(self):
        self.assertEqual(normalize_asr_language("Klingon"), "Klingon")


class TestFunASRLanguageMapping(unittest.TestCase):
    """标准化名称 -> FunASR 语言代码映射。"""

    def test_maps_common_languages(self):
        self.assertEqual(_to_funasr_language("English"), "en")
        self.assertEqual(_to_funasr_language("Chinese"), "zh")
        self.assertEqual(_to_funasr_language("Japanese"), "ja")
        self.assertEqual(_to_funasr_language("Korean"), "ko")

    def test_auto_returns_none(self):
        self.assertIsNone(_to_funasr_language(None))

    def test_unknown_returns_none(self):
        self.assertIsNone(_to_funasr_language("Klingon"))


class TestCleanFunASRText(unittest.TestCase):
    """FunASR 特殊标记清理。"""

    def test_removes_language_tags(self):
        self.assertEqual(_clean_funasr_text("<|zh|>你好世界"), "你好世界")

    def test_removes_emotion_tags(self):
        self.assertEqual(_clean_funasr_text("<|HAPPY|>太好了<|SAD|>"), "太好了")

    def test_preserves_normal_text(self):
        self.assertEqual(_clean_funasr_text("Hello world"), "Hello world")

    def test_removes_multiple_tags(self):
        self.assertEqual(_clean_funasr_text("<|en|><|Music|>Hello<|LAUGHTER|>"), "Hello")


class TestASRConfig(unittest.TestCase):
    """ASRConfig 新字段验证。"""

    def test_default_uses_sensevoice(self):
        config = ASRConfig()
        self.assertEqual(config.model, "iic/SenseVoiceSmall")
        self.assertEqual(config.vad_model, "fsmn-vad")
        self.assertEqual(config.punc_model, "ct-punc")
        self.assertIsNone(config.spk_model)
        self.assertEqual(config.device, "cpu")

    def test_custom_model_and_speaker(self):
        config = ASRConfig(model="paraformer-zh", spk_model="cam++")
        self.assertEqual(config.model, "paraformer-zh")
        self.assertEqual(config.spk_model, "cam++")

    def test_invalid_vad_max_segment_ms(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ASRConfig(vad_max_segment_ms=0)


class TestTranscribe(unittest.TestCase):
    """transcribe 函数集成测试（mock FunASR）。"""

    @patch("subtitle_llm.media.transcriber.SubtitleIO.write_srt")
    def test_transcribe_with_sentence_info(self, mock_write):
        """模拟 FunASR 返回 sentence_info，验证时间戳和文本正确写入字幕。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = [
            {
                "text": "你好世界。再见。",
                "sentence_info": [
                    {"text": "你好世界。", "start": 1000, "end": 3500},
                    {"text": "再见。", "start": 4500, "end": 6000},
                ],
            }
        ]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            audio_path = f.name

        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as f:
            output_path = f.name

        try:
            with patch("funasr.AutoModel", return_value=mock_model):
                result = transcribe(audio_path, "Chinese", output_path)

            self.assertEqual(result, output_path)
            mock_model.generate.assert_called_once()
            mock_write.assert_called_once()

            # 验证写入的字幕内容
            subtitle = mock_write.call_args[0][0]
            self.assertEqual(len(subtitle.entries), 2)
            self.assertEqual(subtitle.entries[0].original_text, "你好世界。")
            self.assertEqual(subtitle.entries[0].start_time, "00:00:01,000")
            self.assertEqual(subtitle.entries[0].end_time, "00:00:03,500")
            self.assertEqual(subtitle.entries[1].original_text, "再见。")
        finally:
            Path(audio_path).unlink(missing_ok=True)
            Path(output_path).unlink(missing_ok=True)

    @patch("subtitle_llm.media.transcriber.SubtitleIO.write_srt")
    def test_transcribe_strips_tags(self, mock_write):
        """验证 FunASR 特殊标记被清除。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = [
            {
                "text": "<|en|><|Music|>Hello world",
                "sentence_info": [
                    {"text": "<|en|><|Music|>Hello world", "start": 0, "end": 2000},
                ],
            }
        ]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            audio_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as f:
            output_path = f.name

        try:
            with patch("funasr.AutoModel", return_value=mock_model):
                transcribe(audio_path, "English", output_path)

            subtitle = mock_write.call_args[0][0]
            self.assertEqual(subtitle.entries[0].original_text, "Hello world")
        finally:
            Path(audio_path).unlink(missing_ok=True)
            Path(output_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
