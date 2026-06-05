import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.llm.types import CompletionUsage
from subtitle_llm.pipeline.llm_trace import LlmTraceRecorder
from subtitle_llm.settings import ModelConfig, ModelProvider


class TestLlmTrace(unittest.TestCase):
    def test_trace_redacts_obvious_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = LlmTraceRecorder(tmp)
            trace_id = recorder.record_call(
                stage="summary-context",
                prompt="Authorization: Bearer sk-test-secret\nDEEPSEEK_API_KEY=sk-deepseek-secret",
                response='{"api_key": "sk-response-secret", "text": "ok"}',
                model_config=ModelConfig(
                    type=ModelProvider.CUSTOM,
                    api_key_env="DEEPSEEK_API_KEY",
                    model="fake",
                    endpoint="https://fake.test",
                ),
                usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                duration_ms=12,
            )

            metadata = json.loads(next(Path(tmp).glob("*.json")).read_text(encoding="utf-8"))
            prompt_text = (Path(tmp) / metadata["files"]["prompt"]).read_text(encoding="utf-8")
            response_text = (Path(tmp) / metadata["files"]["response"]).read_text(encoding="utf-8")

            self.assertEqual(trace_id, "000001")
            self.assertIn("[REDACTED]", prompt_text)
            self.assertIn("[REDACTED]", response_text)
            self.assertNotIn("sk-test-secret", prompt_text)
            self.assertNotIn("sk-response-secret", response_text)


if __name__ == "__main__":
    unittest.main()
