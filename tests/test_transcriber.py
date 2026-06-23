import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.media.asr_backend import (
    AsrCue,
    AsrError,
    LlamacppAsrBackend,
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
    """ASRConfig 新字段验证。"""

    def test_default_uses_llamacpp_binaries(self):
        config = ASRConfig()
        self.assertEqual(config.vad_binary, "llama-funasr-vad")
        self.assertEqual(config.sensevoice_binary, "llama-funasr-sensevoice")
        self.assertEqual(config.sensevoice_model, "sensevoice-small-f16.gguf")
        self.assertEqual(config.model_dir, "./gguf")

    def test_model_path_joins_dir_and_filename(self):
        config = ASRConfig(model_dir="/models/gguf")
        self.assertEqual(config.model_path("fsmn-vad.gguf"), Path("/models/gguf/fsmn-vad.gguf"))

    def test_custom_binaries_and_model(self):
        config = ASRConfig(
            vad_binary="/opt/funasr/llama-funasr-vad",
            sensevoice_binary="/opt/funasr/llama-funasr-sensevoice",
            sensevoice_model="sensevoice-small-q8.gguf",
        )
        self.assertEqual(config.vad_binary, "/opt/funasr/llama-funasr-vad")
        self.assertEqual(config.sensevoice_model, "sensevoice-small-q8.gguf")


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


class TestLlamacppAsrBackend(unittest.TestCase):
    """LlamacppAsrBackend 解析逻辑（mock subprocess，不依赖真实二进制）。"""

    def _make_backend(self):
        return LlamacppAsrBackend(config=ASRConfig(model_dir="./gguf"))

    @patch.object(LlamacppAsrBackend, "_run_binary")
    def test_pairs_vad_timestamps_with_sensevoice_segments(self, mock_run):
        # VAD 输出两段；SenseVoice 整段输出两个 <|en|> 文本片段
        vad_output = "0 3000\n3500 6000\n"
        sensevoice_output = (
            "<|en|><|EMO_UNKNOWN|>hello world<|en|><|EMO_UNKNOWN|>goodbye"
        )
        mock_run.side_effect = [vad_output, sensevoice_output]

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].start_ms, 0)
        self.assertEqual(cues[0].end_ms, 3000)
        self.assertEqual(cues[0].text, "hello world")
        self.assertEqual(cues[1].start_ms, 3500)
        self.assertEqual(cues[1].end_ms, 6000)
        self.assertEqual(cues[1].text, "goodbye")

    @patch.object(LlamacppAsrBackend, "_run_binary")
    def test_strips_tags_in_paired_segments(self, mock_run):
        vad_output = "0 2000\n"
        sensevoice_output = "<|en|><|Music|>Hello world"
        mock_run.side_effect = [vad_output, sensevoice_output]

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")
        self.assertEqual(cues[0].text, "Hello world")

    @patch.object(LlamacppAsrBackend, "_run_binary")
    def test_count_mismatch_falls_back_to_single_cue(self, mock_run):
        # VAD 两段，但 SenseVoice 只切出一段 -> 降级为整段一条
        vad_output = "0 3000\n3500 6000\n"
        sensevoice_output = "<|en|>only one segment here"
        mock_run.side_effect = [vad_output, sensevoice_output]

        cues = self._make_backend().transcribe("/tmp/a.wav", "English")
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].start_ms, 0)
        self.assertEqual(cues[0].end_ms, 6000)
        self.assertEqual(cues[0].text, "only one segment here")

    @patch.object(LlamacppAsrBackend, "_run_binary")
    def test_empty_results_produce_no_cues(self, mock_run):
        mock_run.side_effect = ["", ""]
        self.assertEqual(self._make_backend().transcribe("/tmp/a.wav", "English"), [])

    @patch("subtitle_llm.media.asr_backend.subprocess.run")
    def test_run_binary_wraps_missing_binary_as_asr_error(self, mock_run):
        mock_run.side_effect = FileNotFoundError(2, "No such file", "llama-funasr-vad")
        backend = self._make_backend()
        with self.assertRaises(AsrError):
            backend._run_binary(["llama-funasr-vad", "-m", "x"], label="VAD")

    @patch("subtitle_llm.media.asr_backend.subprocess.run")
    def test_run_binary_wraps_nonzero_exit_as_asr_error(self, mock_run):
        import subprocess as sp

        mock_run.side_effect = sp.CalledProcessError(1, "cmd", stderr="boom")
        backend = self._make_backend()
        with self.assertRaises(AsrError):
            backend._run_binary(["llama-funasr-vad", "-m", "x"], label="VAD")


if __name__ == "__main__":
    unittest.main()
