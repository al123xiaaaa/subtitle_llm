"""transcribe.cpp 后端：输出解析、语言映射、后端工厂。"""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import MagicMock, patch

from subtitle_llm.media.asr_backend_transcribe_cpp import (
    TranscribeCppBackend,
    normalize_iso_language,
    parse_segment_output,
)
from subtitle_llm.media.asr_models import resolve_asr_config
from subtitle_llm.media.transcriber import create_asr_backend
from subtitle_llm.media.asr_backend import FunasrAsrBackend


class TestParseSegmentOutput(unittest.TestCase):
    def test_parses_segment_lines(self):
        # 真实 CLI 输出的段行带前导缩进
        stdout = (
            "  [   0.00 ->    6.54] I'm asking you to pretend.\n"
            "  [   8.53 ->   28.22]  Just one more time, because if we lose.\n"
        )
        cues = parse_segment_output(stdout)
        self.assertEqual(len(cues), 2)
        self.assertEqual((cues[0].start_ms, cues[0].end_ms), (0, 6540))
        self.assertEqual(cues[0].text, "I'm asking you to pretend.")
        self.assertEqual(cues[1].text, "Just one more time, because if we lose.")

    def test_skips_log_lines_and_empty_segments(self):
        stdout = (
            "ggml_metal_device_init: testing tensor API\n"
            "[   0.00 ->    1.00] \n"
            "  realtime:   59x (3426.6 ms for 201.6 s)\n"
            "[   1.00 ->    2.00] hello world\n"
        )
        cues = parse_segment_output(stdout)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "hello world")


class TestNormalizeIsoLanguage(unittest.TestCase):
    def test_maps_names_to_iso(self):
        self.assertEqual(normalize_iso_language("English"), "en")
        self.assertEqual(normalize_iso_language("Chinese"), "zh")
        self.assertEqual(normalize_iso_language(None), "auto")
        self.assertEqual(normalize_iso_language("Cantonese"), "yue")


class TestBackendFactory(unittest.TestCase):
    def test_funasr_is_default(self):
        backend = create_asr_backend(resolve_asr_config(profile="fun-asr-nano"))
        self.assertIsInstance(backend, FunasrAsrBackend)

    def test_transcribe_cpp_profile_selects_cpp_backend(self):
        backend = create_asr_backend(resolve_asr_config(profile="whisper-turbo"))
        self.assertIsInstance(backend, TranscribeCppBackend)
        self.assertEqual(backend.config.gguf_file, "whisper-large-v3-turbo-Q8_0.gguf")
        self.assertEqual(backend.config.language_style, "iso")


class TestTranscribeCppBackendRun(unittest.TestCase):
    def test_transcribe_invokes_cli_and_parses(self):
        config = resolve_asr_config(profile="whisper-turbo")
        backend = TranscribeCppBackend(config=config)
        fake_result = MagicMock(returncode=0, stderr="")
        fake_result.stdout = "[   0.00 ->    6.54] hello there.\n"
        with (
            patch("subtitle_llm.media.asr_backend_transcribe_cpp._resolve_transcribe_cli", return_value="/bin/fake-cli"),
            patch("subtitle_llm.media.asr_backend_transcribe_cpp._resolve_gguf_model", return_value="/tmp/fake.gguf"),
            patch("subtitle_llm.media.asr_backend_transcribe_cpp._ensure_16k_mono_wav", side_effect=lambda p: str(p)),
            patch("subprocess.run", return_value=fake_result) as run_mock,
        ):
            cues = backend.transcribe("/tmp/a.wav", "English")
        cmd = run_mock.call_args.args[0]
        self.assertEqual(cmd[0], "/bin/fake-cli")
        self.assertIn("--timestamps", cmd)
        self.assertIn("segment", cmd)
        self.assertIn("-l", cmd)
        self.assertIn("en", cmd)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].end_ms, 6540)

    def test_cli_failure_raises(self):
        config = resolve_asr_config(profile="whisper-turbo")
        backend = TranscribeCppBackend(config=config)
        fake_result = MagicMock(returncode=1, stderr="boom", stdout="")
        with (
            patch("subtitle_llm.media.asr_backend_transcribe_cpp._resolve_transcribe_cli", return_value="/bin/fake-cli"),
            patch("subtitle_llm.media.asr_backend_transcribe_cpp._resolve_gguf_model", return_value="/tmp/fake.gguf"),
            patch("subtitle_llm.media.asr_backend_transcribe_cpp._ensure_16k_mono_wav", side_effect=lambda p: str(p)),
            patch("subprocess.run", return_value=fake_result),
        ):
            with self.assertRaises(RuntimeError):
                backend.transcribe("/tmp/a.wav", "English")


if __name__ == "__main__":
    unittest.main()


class TestEnsure16kMonoWav(unittest.TestCase):
    def test_already_16k_mono_passthrough(self):
        import wave
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setframerate(16000)
            wav.setsampwidth(2)
            wav.writeframes(b"\x00" * 3200)
        from subtitle_llm.media.asr_backend_transcribe_cpp import _ensure_16k_mono_wav
        self.assertEqual(_ensure_16k_mono_wav(path), path)
