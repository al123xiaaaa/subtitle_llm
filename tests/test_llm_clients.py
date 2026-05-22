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


if __name__ == "__main__":
    unittest.main()
