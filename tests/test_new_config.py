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
    def test_optional_positive_output_budget(self):
        from pydantic import ValidationError
        from subtitle_llm.settings import ModelConfig

        base = dict(type="openai", model="generic", api_key_env="TEST_KEY")
        self.assertIsNone(ModelConfig(**base).max_tokens)
        for budget in (None, 1, 8192, 32768):
            with self.subTest(budget=budget):
                model = ModelConfig(**base, max_tokens=budget)
                self.assertEqual(model.max_tokens, budget)
                self.assertEqual(ModelConfig.model_validate_json(model.model_dump_json()).max_tokens, budget)
        for budget in (0, -1, 1.5):
            with self.subTest(invalid=budget), self.assertRaises(ValidationError):
                ModelConfig(**base, max_tokens=budget)
        self.assertIsNone(load_config().translation_model.max_tokens)
        self.assertIsNone(load_config().summary_model.max_tokens)

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
        self.assertEqual(config.asr.model_name, ASRConfig().model_name)
        self.assertEqual(config.asr.punc_model, ASRConfig().punc_model)

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
