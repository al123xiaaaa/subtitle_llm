import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.media.asr_backend import (
    AsrCue,
    FunasrAsrBackend,
    clean_sensevoice_text,
)
from subtitle_llm.media.transcriber import (
    build_subtitle,
    normalize_asr_language,
    transcribe_with_backend,
)
from subtitle_llm.settings import ASRConfig


class StubBackend:
    """注入用的 ASR 后端桩，返回预设的识别片段。"""

    def __init__(self, cues: list[AsrCue]):
        self._cues = cues

    def transcribe(self, audio_path, language, progress=None):
        return list(self._cues)


class TestLanguageNormalization(unittest.TestCase):
    """语言标识标准化测试。"""

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


class TestCleanSensevoiceText(unittest.TestCase):
    """SenseVoice 特殊标记清理。"""

    def test_removes_language_tags(self):
        self.assertEqual(clean_sensevoice_text("<|zh|>你好世界"), "你好世界")

    def test_removes_emotion_and_event_tags(self):
        self.assertEqual(clean_sensevoice_text("<|HAPPY|>太好了<|SAD|>"), "太好了")

    def test_removes_nospeech_tags(self):
        self.assertEqual(clean_sensevoice_text("<|nospeech|><|Event_UNK|> the"), "the")

    def test_preserves_normal_text(self):
        self.assertEqual(clean_sensevoice_text("Hello world"), "Hello world")

    def test_removes_multiple_tags(self):
        self.assertEqual(clean_sensevoice_text("<|en|><|Music|>Hello<|LAUGHTER|>"), "Hello")


class TestASRConfig(unittest.TestCase):
    """ASRConfig funasr SDK 字段验证。"""

    def test_default_uses_funasr_sdk(self):
        config = ASRConfig()
        self.assertEqual(config.model_name, "FunAudioLLM/SenseVoiceSmall")
        self.assertEqual(config.punc_model, "ct-punc")
        self.assertEqual(config.spk_model, "cam++")
        self.assertEqual(config.max_single_segment_time, 8000)
        self.assertEqual(config.device, "cpu")

    def test_custom_model_and_device(self):
        config = ASRConfig(
            model_name="FunAudioLLM/Fun-ASR-Nano-2512",
            device="cuda",
            max_single_segment_time=15000,
        )
        self.assertEqual(config.model_name, "FunAudioLLM/Fun-ASR-Nano-2512")
        self.assertEqual(config.device, "cuda")
        self.assertEqual(config.max_single_segment_time, 15000)


class TestBuildSubtitle(unittest.TestCase):
    """把 AsrCue 组装成时间轴字幕。"""

    def test_assembles_cues_with_timestamps(self):
        cues = [
            AsrCue(start_ms=1000, end_ms=3500, text="你好世界。"),
            AsrCue(start_ms=4500, end_ms=6000, text="再见。"),
        ]
        subtitle = build_subtitle(cues)
        self.assertEqual(len(subtitle.entries), 2)
        self.assertEqual(subtitle.entries[0].original_text, "你好世界。")
        self.assertEqual(subtitle.entries[0].start_time, "00:00:01,000")
        self.assertEqual(subtitle.entries[0].end_time, "00:00:03,500")
        self.assertEqual(subtitle.entries[1].original_text, "再见。")
        self.assertEqual(subtitle.entries[1].index, 2)

    def test_enforces_minimum_duration(self):
        cues = [AsrCue(start_ms=1000, end_ms=1050, text="短")]  # 50ms -> 拉到 100ms
        subtitle = build_subtitle(cues)
        self.assertEqual(subtitle.entries[0].start_time, "00:00:01,000")
        self.assertEqual(subtitle.entries[0].end_time, "00:00:01,100")

    def test_empty_cues_produce_empty_subtitle(self):
        self.assertEqual(len(build_subtitle([]).entries), 0)


class TestTranscribeWithBackend(unittest.TestCase):
    """transcribe_with_backend 用注入后端验证组装与写出。"""

    @patch("subtitle_llm.media.transcriber.SubtitleIO.write_srt")
    def test_transcribe_writes_subtitle_from_backend_cues(self, mock_write):
        cues = [
            AsrCue(start_ms=1000, end_ms=3500, text="你好世界。"),
            AsrCue(start_ms=4500, end_ms=6000, text="再见。"),
        ]
        backend = StubBackend(cues)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            audio_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as f:
            output_path = f.name

        try:
            result = transcribe_with_backend(audio_path, "Chinese", output_path, backend)
            self.assertEqual(result, output_path)
            mock_write.assert_called_once()

            subtitle = mock_write.call_args[0][0]
            self.assertEqual(len(subtitle.entries), 2)
            self.assertEqual(subtitle.entries[0].original_text, "你好世界。")
            self.assertEqual(subtitle.entries[0].start_time, "00:00:01,000")
            self.assertEqual(subtitle.entries[0].end_time, "00:00:03,500")
            self.assertEqual(subtitle.entries[1].original_text, "再见。")
        finally:
            Path(audio_path).unlink(missing_ok=True)
            Path(output_path).unlink(missing_ok=True)


class TestFunasrAsrBackend(unittest.TestCase):
    """FunasrAsrBackend 解析逻辑（mock funasr.AutoModel，不依赖真实模型）。"""

    def _make_backend(self):
        return FunasrAsrBackend(config=ASRConfig())

    def _mock_auto_model(self, mock_am_cls, generate_result):
        """构造一个 mock AutoModel，其 generate 返回 generate_result。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = generate_result
        mock_am_cls.return_value = mock_model
        return mock_model

    @patch("subtitle_llm.media.asr_backend.FunasrAsrBackend._get_or_load_model")
    def test_sentence_info_produces_timed_cues(self, mock_load):
        """sentence_info 正常路径：每段产出带时间戳的 AsrCue。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = [{
            "sentence_info": [
                {"start": 0, "end": 3000, "text": "hello world"},
                {"start": 3500, "end": 6000, "text": "goodbye"},
            ]
        }]
        mock_load.return_value = mock_model

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].start_ms, 0)
        self.assertEqual(cues[0].end_ms, 3000)
        self.assertEqual(cues[0].text, "hello world")
        self.assertEqual(cues[1].start_ms, 3500)
        self.assertEqual(cues[1].end_ms, 6000)
        self.assertEqual(cues[1].text, "goodbye")

    @patch("subtitle_llm.media.asr_backend.FunasrAsrBackend._get_or_load_model")
    def test_prefers_word_timestamps_over_drifting_sentence_info(self, mock_load):
        """sentence_info 句级时间漂移时，优先使用顶层 words/timestamp 重建原始时间轴。

        这里 sentence_info 不带段内子时间戳（timestamp 字段缺失），对齐路径会失败并
        退化为按标点断句，时间仍取自顶层 timestamp（无漂移）。
        """
        mock_model = MagicMock()
        mock_model.generate.return_value = [{
            "sentence_info": [
                {
                    "start": 100020,
                    "end": 107770,
                    "text": "or if you feel shaking.Take cover under furniture.In sacramento.",
                }
            ],
            "words": [
                "or",
                "if",
                "you",
                "feel",
                "shaking",
                ".",
                "Take",
                "cover",
                ".",
                "In",
                "sacramento",
                ".",
            ],
            "timestamp": [
                [100020, 100080],
                [100260, 100320],
                [100440, 100500],
                [100620, 100680],
                [100860, 101160],
                [101160, 101220],
                [115470, 115530],
                [115830, 116250],
                [116250, 116310],
                [120160, 120220],
                [120460, 121000],
                [123640, 123700],
            ],
        }]
        mock_load.return_value = mock_model

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")

        self.assertEqual(len(cues), 3)
        self.assertEqual(cues[-1].text, "In sacramento.")
        self.assertEqual(cues[-1].start_ms, 120160)
        self.assertEqual(cues[-1].end_ms, 123700)

    @patch("subtitle_llm.media.asr_backend.FunasrAsrBackend._get_or_load_model")
    def test_sentence_alignment_uses_semantic_segments(self, mock_load):
        """方向 A：sentence_info 子时间戳对齐顶层词轴，按语义段切而非按句号切。

        关键：sentence_info 第一段文本含中间句号 "workflow. I think"，对齐后应保留在同一
        条 cue（不会在 workflow. 处断开），且起止毫秒取自顶层 timestamp（无漂移）。
        """
        mock_model = MagicMock()
        mock_model.generate.return_value = [{
            "sentence_info": [
                {
                    "start": 1000000,  # 故意漂移的句级时间，不应被采用
                    "end": 1003000,
                    "text": "people love your workflow. I think",
                    "timestamp": [[30, 90], [330, 390], [990, 1050], [2070, 2130], [2250, 2310], [2790, 2850]],
                },
                {
                    "start": 1004000,
                    "end": 1007000,
                    "text": "this is great",
                    "timestamp": [[3210, 3270], [3450, 3510], [3690, 3750]],
                },
            ],
            "words": ["people", "love", "your", "workflow", ".", "I", "think", "this", "is", "great", "."],
            "timestamp": [
                [30, 90], [330, 390], [990, 1050], [2070, 2130], [2130, 2190],
                [2250, 2310], [2790, 2850], [3210, 3270], [3450, 3510], [3690, 3750], [3750, 3810],
            ],
        }]
        mock_load.return_value = mock_model

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")

        # 语义两段，而非按句号切成三段。
        self.assertEqual(len(cues), 2)
        # 第一段保留中间句号，时间取自顶层 timestamp（30..2850，不是漂移的 1000xxx）。
        self.assertEqual(cues[0].text, "people love your workflow. I think")
        self.assertEqual(cues[0].start_ms, 30)
        self.assertEqual(cues[0].end_ms, 2850)
        # 第二段时间同样取自顶层轴。
        self.assertEqual(cues[1].start_ms, 3210)
        self.assertEqual(cues[1].end_ms, 3750)

    @patch("subtitle_llm.media.asr_backend.FunasrAsrBackend._get_or_load_model")
    def test_sentence_alignment_strips_leading_punctuation(self, mock_load):
        """方向 A：cam++ 把上一句句末标点算进下段首位时，剥离前导孤立标点。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = [{
            "sentence_info": [
                {
                    "start": 0, "end": 2000,
                    "text": "rather than the",
                    "timestamp": [[1000, 1060], [1120, 1180], [1240, 1300]],
                },
                {
                    "start": 2500, "end": 4000,
                    "text": ". Po request",  # 前导句号来自上一句句末
                    "timestamp": [[1360, 1420], [1480, 1540], [1600, 1660]],
                },
            ],
            "words": ["rather", "than", "the", ".", "Po", "request"],
            "timestamp": [
                [1000, 1060], [1120, 1180], [1240, 1300],
                [1360, 1420], [1480, 1540], [1600, 1660],
            ],
        }]
        mock_load.return_value = mock_model

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")

        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].text, "rather than the")
        # 第二段剥离前导句号，且起点对齐到 'Po' 的时间戳。
        self.assertEqual(cues[1].text, "Po request")
        self.assertEqual(cues[1].start_ms, 1480)

    @patch("subtitle_llm.media.asr_backend.FunasrAsrBackend._get_or_load_model")
    def test_sentence_alignment_falls_back_when_subtimestamps_off_axis(self, mock_load):
        """方向 A：子时间戳不在顶层轴上时，整体退化为按标点断句。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = [{
            "sentence_info": [
                {
                    "start": 0, "end": 2000,
                    "text": "hello world",
                    "timestamp": [[99999, 99999]],  # 不在顶层轴上
                },
            ],
            "words": ["hello", "world", "."],
            "timestamp": [[1000, 1060], [1120, 1180], [1180, 1240]],
        }]
        mock_load.return_value = mock_model

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")

        # 对齐失败，退化为标点断句：一句。
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "hello world.")
        self.assertEqual(cues[0].start_ms, 1000)
        self.assertEqual(cues[0].end_ms, 1240)

    @patch("subtitle_llm.media.asr_backend.FunasrAsrBackend._get_or_load_model")
    def test_word_timestamp_rebuild_preserves_decimal_numbers(self, mock_load):
        """词级重建不能把 5.6 里的小数点当句号切开。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = [{
            "words": ["after", "a", "5", ".", "6", "magnitude", "quake", "."],
            "timestamp": [
                [1000, 1060],
                [1120, 1180],
                [1240, 1300],
                [1300, 1360],
                [1360, 1420],
                [1480, 1540],
                [1600, 1660],
                [1660, 1720],
            ],
        }]
        mock_load.return_value = mock_model

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")

        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "after a 5.6 magnitude quake.")

    @patch("subtitle_llm.media.asr_backend.FunasrAsrBackend._get_or_load_model")
    def test_word_timestamp_rebuild_cleans_sentencepiece_and_joined_tokens(self, mock_load):
        """词级重建清理 SentencePiece 标记，并合并常见拆分 token。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = [{
            "words": ["▁most", "people", "wasn", "'", "t", "1", "0", "0", "miles", "."],
            "timestamp": [
                [1000, 1060],
                [1120, 1180],
                [1240, 1300],
                [1300, 1360],
                [1360, 1420],
                [1480, 1540],
                [1540, 1600],
                [1600, 1660],
                [1720, 1780],
                [1780, 1840],
            ],
        }]
        mock_load.return_value = mock_model

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")

        self.assertEqual(cues[0].text, "most people wasn't 100 miles.")

    @patch("subtitle_llm.media.asr_backend.FunasrAsrBackend._get_or_load_model")
    def test_strips_sensevoice_tags(self, mock_load):
        """SenseVoice 标签（<|en|>、<|EMO_UNKNOWN|> 等）被清除。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = [{
            "sentence_info": [
                {"start": 0, "end": 2000, "text": "<|en|><|Music|>Hello world"},
            ]
        }]
        mock_load.return_value = mock_model

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")
        self.assertEqual(cues[0].text, "Hello world")

    @patch("subtitle_llm.media.asr_backend.FunasrAsrBackend._get_or_load_model")
    def test_empty_text_segments_filtered(self, mock_load):
        """空文本段（如 nospeech）被过滤。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = [{
            "sentence_info": [
                {"start": 0, "end": 1000, "text": "hello"},
                {"start": 1500, "end": 2000, "text": "<|nospeech|>"},
                {"start": 2500, "end": 3000, "text": "world"},
            ]
        }]
        mock_load.return_value = mock_model

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].text, "hello")
        self.assertEqual(cues[1].text, "world")

    @patch("subtitle_llm.media.asr_backend.FunasrAsrBackend._get_or_load_model")
    def test_falls_back_to_single_cue_without_sentence_info(self, mock_load):
        """无 sentence_info 时降级为整段一条。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = [{"text": "only one segment here"}]
        mock_load.return_value = mock_model

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "only one segment here")

    @patch("subtitle_llm.media.asr_backend.FunasrAsrBackend._get_or_load_model")
    def test_empty_result_produces_no_cues(self, mock_load):
        """空结果产出 0 条 cue。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = []
        mock_load.return_value = mock_model

        self.assertEqual(self._make_backend().transcribe("/tmp/a.wav", "English"), [])

    def test_normalize_language_maps_names_to_codes(self):
        """语言名称（English）映射为 funasr 代码（en）。"""
        self.assertEqual(FunasrAsrBackend._normalize_language("English"), "en")
        self.assertEqual(FunasrAsrBackend._normalize_language("Chinese"), "zh")
        self.assertEqual(FunasrAsrBackend._normalize_language(None), "auto")
        self.assertEqual(FunasrAsrBackend._normalize_language(""), "auto")

    @patch("subtitle_llm.media.asr_backend.FunasrAsrBackend._get_or_load_model")
    def test_language_passed_to_generate(self, mock_load):
        """用户指定的语言被透传给 generate（约束识别语言）。"""
        mock_model = MagicMock()
        mock_model.generate.return_value = [{"sentence_info": []}]
        mock_load.return_value = mock_model

        self._make_backend().transcribe("/tmp/a.wav", "English")
        call_kwargs = mock_model.generate.call_args.kwargs
        self.assertEqual(call_kwargs["language"], "en")


if __name__ == "__main__":
    unittest.main()
