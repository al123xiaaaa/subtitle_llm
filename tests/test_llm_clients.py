import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.llm.clients import CustomHTTPChatClient
from subtitle_llm.settings import ModelConfig, ModelProvider


class TestCustomHTTPChatClient(unittest.TestCase):
    def test_custom_client_sends_repeat_penalty_when_configured(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        config = ModelConfig(
            type=ModelProvider.CUSTOM,
            api_key_env="HYMT2_API_KEY",
            model="/tmp/model.gguf",
            endpoint="http://127.0.0.1:8123/v1",
            top_k=20,
            repeat_penalty=1.05,
        )

        with patch("subtitle_llm.llm.clients.requests.post", return_value=response) as post:
            client = CustomHTTPChatClient(api_key="local-no-key-needed", base_url="http://127.0.0.1:8123/v1")
            result = client.create_completion(config, [{"role": "user", "content": "hello"}])

        self.assertEqual(result.content, "ok")
        self.assertEqual(post.call_args.kwargs["json"]["top_k"], 20)
        self.assertEqual(post.call_args.kwargs["json"]["repeat_penalty"], 1.05)


class TestOptionalBudgetAndMetadata(unittest.TestCase):
    def test_openai_omits_budget_and_extracts_reasoning_without_text(self):
        from types import SimpleNamespace
        from subtitle_llm.llm.clients import OpenAIChatClient

        for budget in (None, 8192):
            with self.subTest(budget=budget), patch("openai.OpenAI") as sdk:
                sdk.return_value.chat.completions.create.return_value = SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=" ok ", reasoning_content="PRIVATE"),
                                             finish_reason="length")],
                    usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30,
                                          completion_tokens_details=SimpleNamespace(reasoning_tokens=15)),
                )
                client = OpenAIChatClient("fake", None)
                config = ModelConfig(type="openai", model="generic", api_key_env="KEY", max_tokens=budget)
                result = client.create_completion(config, [{"role": "user", "content": "hello"}])
                params = sdk.return_value.chat.completions.create.call_args.kwargs
                self.assertEqual("max_tokens" in params, budget is not None)
                if budget is not None:
                    self.assertEqual(params["max_tokens"], budget)
                self.assertEqual(result.content, "ok")
                self.assertEqual(result.finish_reason, "length")
                self.assertEqual(result.usage.reasoning_tokens, 15)
                self.assertEqual(result.usage.total_tokens, 30)

    def test_custom_optional_budget_and_nullable_reasoning(self):
        for budget in (None, 8192):
            for reasoning in (None, 0, 12):
                with self.subTest(budget=budget, reasoning=reasoning):
                    usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
                    if reasoning is not None:
                        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
                    response = Mock()
                    response.json.return_value = {
                        "choices": [{"message": {"content": "ok", "reasoning_content": "PRIVATE"},
                                     "finish_reason": "stop"}], "usage": usage,
                    }
                    config = ModelConfig(type="custom", model="generic", api_key_env="KEY", max_tokens=budget)
                    with patch("subtitle_llm.llm.clients.requests.post", return_value=response) as post:
                        result = CustomHTTPChatClient("fake", "https://fake.test").create_completion(config, [])
                    self.assertEqual("max_tokens" in post.call_args.kwargs["json"], budget is not None)
                    if budget is not None:
                        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], budget)
                    self.assertEqual(result.usage.reasoning_tokens, reasoning)
                    self.assertEqual(result.usage.total_tokens, 30)
                    self.assertEqual(result.finish_reason, "stop")
                    self.assertEqual(result.content, "ok")

    def test_gemini_uses_sdk_usage_and_normalizes_stop_reasons(self):
        from google.ai.generativelanguage import Candidate, Content, GenerateContentResponse, Part
        from subtitle_llm.llm.clients import GeminiChatClient

        for budget in (None, 8192):
            for reason, normalized in ((Candidate.FinishReason.STOP, "stop"),
                                       (Candidate.FinishReason.MAX_TOKENS, "length"),
                                       (Candidate.FinishReason.SAFETY, "content_filter")):
                with self.subTest(budget=budget, reason=reason), patch("google.generativeai.configure"), \
                        patch("google.generativeai.GenerativeModel") as model:
                    response = GenerateContentResponse(
                        candidates=[Candidate(content=Content(parts=[Part(text="ok")] if normalized == "stop" else []),
                                              finish_reason=reason)],
                        usage_metadata=GenerateContentResponse.UsageMetadata(
                            prompt_token_count=10, candidates_token_count=20, total_token_count=35),
                    )
                    model.return_value.start_chat.return_value.send_message.return_value = response
                    config = ModelConfig(type="gemini", model="generic", api_key_env="KEY", max_tokens=budget)
                    result = GeminiChatClient("fake").create_completion(config, [{"role": "user", "content": "hi"}])
                    params = model.call_args.kwargs["generation_config"]
                    self.assertEqual("max_output_tokens" in params, budget is not None)
                    if budget is not None:
                        self.assertEqual(params["max_output_tokens"], budget)
                    self.assertEqual(result.finish_reason, normalized)
                    self.assertEqual(result.usage.total_tokens, 35)
                    self.assertEqual(result.usage.completion_tokens, 20)
                    self.assertIsNone(result.usage.reasoning_tokens)
                    self.assertEqual(result.content, "ok" if normalized == "stop" else "")

    def test_usage_reasoning_round_trip_and_aggregation(self):
        from subtitle_llm.llm.types import CompletionUsage

        usage = CompletionUsage()
        self.assertIsNone(CompletionUsage.from_any(usage.to_dict()).reasoning_tokens)
        usage.add(CompletionUsage(total_tokens=10, reasoning_tokens=3))
        usage.add(CompletionUsage(total_tokens=20))
        self.assertEqual(usage.total_tokens, 30)
        self.assertEqual(usage.reasoning_tokens, 3)  # 仅累计厂商已报告的明细
        self.assertEqual(CompletionUsage.from_any(usage.to_dict()).reasoning_tokens, 3)
        self.assertEqual(CompletionUsage.from_any({"reasoning_tokens": 0}).reasoning_tokens, 0)


if __name__ == "__main__":
    unittest.main()
