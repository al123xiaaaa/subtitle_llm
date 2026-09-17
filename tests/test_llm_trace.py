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
    def test_summary_translation_and_error_traces_preserve_metadata(self):
        from unittest.mock import Mock
        from subtitle_llm.domain import SubtitleEntry
        from subtitle_llm.llm.types import CompletionResult, OutputBudgetExhaustedError
        from subtitle_llm.pipeline.chunk_translator import ChunkTranslator
        from subtitle_llm.pipeline.context import ContextService

        for budget in (None, 8192):
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as tmp:
                recorder = LlmTraceRecorder(tmp)
                config = ModelConfig(type="custom", model="generic", api_key_env="KEY", max_tokens=budget)
                usage = CompletionUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30, reasoning_tokens=15)
                client = Mock()
                client.create_completion.return_value = CompletionResult("summary", usage, "stop")
                ContextService(client, config, trace_recorder=recorder).build_context("hello", "Chinese")
                translator = ChunkTranslator(client, config, trace_recorder=recorder)
                chunk = [SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "Hello")]
                translator._execute_operation(stage="test-translation", prompt="hello", chunk=chunk,
                                              usage=CompletionUsage())
                client.create_completion.return_value = CompletionResult("", usage, "length")
                def invalid(_content):
                    raise ValueError("invalid response")
                with self.assertRaises(OutputBudgetExhaustedError):
                    translator._execute_operation(stage="test-parse-failure", prompt="hello", chunk=chunk,
                                                  usage=CompletionUsage(), process=invalid)
                client.create_completion.side_effect = RuntimeError("offline")
                with self.assertRaises(RuntimeError):
                    ContextService(client, config, trace_recorder=recorder).build_context("hello", "Chinese")
                traces = [json.loads(p.read_text()) for p in sorted(Path(tmp).glob("*.json"))]
                self.assertEqual(len(traces), 4)
                self.assertEqual([t["finish_reason"] for t in traces], ["stop", "stop", "length", None])
                self.assertTrue(all(t["requested_max_tokens"] == budget for t in traces))
                for trace in traces[:3]:
                    self.assertEqual(trace["usage"]["reasoning_tokens"], 15)
                    self.assertEqual(trace["usage"]["total_tokens"], 30)
                self.assertIsNone(traces[-1]["usage"]["reasoning_tokens"])

    def test_semantic_fallback_failure_preserves_finish_reason(self):
        from unittest.mock import Mock, patch
        from subtitle_llm.pipeline.chunk_translator import ChunkTranslator

        with tempfile.TemporaryDirectory() as tmp:
            recorder = LlmTraceRecorder(tmp)
            config = ModelConfig(type="custom", model="generic", api_key_env="KEY")
            translator = ChunkTranslator(Mock(), config, trace_recorder=recorder)
            with patch("subtitle_llm.pipeline.chunk_translator.process_timed_cue_json_translation",
                       side_effect=ValueError("timed failure")), \
                    patch("subtitle_llm.pipeline.chunk_translator.process_semantic_json_translation",
                          side_effect=ValueError("semantic failure")):
                with self.assertRaisesRegex(ValueError, "timed failure"):
                    translator._process_semantic_timed_response(
                        "invalid", [], [], target_language="Chinese", stage="semantic-cue-rough",
                        prompt="hello", usage=CompletionUsage(), duration_ms=1,
                        chunk_index=1, finish_reason="length",
                    )
            trace = json.loads(next(Path(tmp).glob("*.json")).read_text())
            self.assertEqual(trace["finish_reason"], "length")
            self.assertIsNone(trace["requested_max_tokens"])

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
