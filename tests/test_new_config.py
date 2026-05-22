import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.settings import ASRConfig, ConfigError, load_config


CONFIG_YAML = """
config_version: "2"
summary_model:
  provider: "gemini"
  api_key_env: "SUMMARY_KEY"
  model: "summary"
translation_model:
  provider: "openai"
  api_key_env: "TRANSLATION_KEY"
  model: "translate"
  endpoint: "https://example.test/v1"
pipeline:
  chunk_size: 2
  threads: 1
  context_window_size: 1
"""


class TestNewConfig(unittest.TestCase):
    def test_load_config_and_resolve_env_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(CONFIG_YAML, encoding="utf-8")
            os.environ["TRANSLATION_KEY"] = "secret"
            config = load_config(path)

            self.assertEqual(config.translation_model.provider.value, "openai")
            self.assertEqual(config.pipeline.chunk_size, 2)
            self.assertEqual(config.translation_model.require_api_key(), "secret")

    def test_missing_env_api_key_fails_before_client_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(CONFIG_YAML, encoding="utf-8")
            os.environ.pop("SUMMARY_KEY", None)
            config = load_config(path)

            with self.assertRaises(ConfigError):
                config.summary_model.require_api_key()

    def test_bundled_default_config_uses_schema_defaults(self):
        config = load_config()

        self.assertEqual(config.config_version, "2")
        self.assertEqual(config.default_output_format, "source-first")
        self.assertEqual(config.pipeline.chunk_size, 34)
        self.assertEqual(config.asr.max_audio_length, ASRConfig().max_audio_length)
        self.assertTrue(config.asr.prefer_local_cache)

    def test_hy_mt2_7b_config_loads_sampling_settings(self):
        config = load_config(PROJECT_ROOT / "hy-mt2-7b-local.yaml")

        self.assertEqual(config.pipeline.chunk_size, 12)
        self.assertEqual(
            config.translation_model.model,
            "/Users/xiaguangwei/.cache/hy-mt2/Hy-MT2-7B-Q4_K_M.gguf",
        )
        self.assertEqual(config.translation_model.top_k, 20)
        self.assertEqual(config.translation_model.repeat_penalty, 1.05)
        self.assertEqual(config.translation_model.request_timeout_seconds, 1800)


if __name__ == "__main__":
    unittest.main()
