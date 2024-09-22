from src.services.llm_clients.llm_client import LLMClient
from typing import List, Dict, Any
import requests
import time
import logging

logger = logging.getLogger(__name__)


class CustomLLMClient(LLMClient):
    def __init__(self, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url

    def create_completion(
        self, config: Dict[str, Any], messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "model": config["model"],
            "messages": messages,
            "max_tokens": config["max_tokens"],
            "temperature": config.get("temperature", 0.5),
            "top_p": config.get("top_p", 1.0),
            "top_k": config.get("top_k", 50),
            "frequency_penalty": config.get("frequency_penalty", 0.0),
            "n": config.get("n", 1),
            "stream": config.get("stream", False),
        }

        max_retries = 6
        retry_delay = 60

        for attempt in range(max_retries):
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=data
                )
                response.raise_for_status()
                result = response.json()
                content = result["choices"][0]["message"]["content"].strip()

                # 计算令牌使用情况
                prompt_tokens = self.num_tokens_from_messages(messages, config["model"])
                completion_tokens = self.num_tokens_from_messages(
                    [{"content": content}], config["model"]
                )
                usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                }
                return {"content": content, "usage": usage}
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    logger.warning(
                        f"Rate limit exceeded. Retrying in {retry_delay} seconds..."
                    )
                    time.sleep(retry_delay)
                else:
                    logger.error(f"HTTP error occurred: {e}")
                    raise
            except requests.exceptions.RequestException as e:
                logger.error(f"Request exception: {e}")
                raise

        raise Exception("Max retries exceeded")
