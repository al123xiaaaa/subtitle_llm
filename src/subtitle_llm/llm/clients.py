from __future__ import annotations

import logging
import time
from typing import Any, cast

import requests

from subtitle_llm.llm.rate_limiter import RateLimiter
from subtitle_llm.llm.token_counter import num_tokens_from_messages
from subtitle_llm.llm.types import CompletionResult, CompletionUsage
from subtitle_llm.settings import ModelConfig

logger = logging.getLogger(__name__)


class BaseClient:
    def __init__(self, api_key: str, rate_limiter: RateLimiter | None = None):
        self.api_key = api_key
        self.rate_limiter = rate_limiter

    def _acquire(self) -> None:
        if self.rate_limiter:
            self.rate_limiter.acquire()

    def _estimated_usage(
        self,
        config: ModelConfig,
        messages: list[dict[str, str]],
        content: str,
    ) -> CompletionUsage:
        prompt_tokens = num_tokens_from_messages(messages, config.model)
        completion_tokens = num_tokens_from_messages([{"role": "assistant", "content": content}], config.model)
        return CompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )


class OpenAIChatClient(BaseClient):
    def __init__(self, api_key: str, base_url: str | None, rate_limiter: RateLimiter | None = None):
        super().__init__(api_key, rate_limiter)
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def create_completion(self, config: ModelConfig, messages: list[dict[str, str]]) -> CompletionResult:
        self._acquire()
        response = self.client.chat.completions.create(
            model=config.model,
            messages=cast(Any, messages),
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            top_p=config.top_p,
            frequency_penalty=config.frequency_penalty,
            presence_penalty=config.presence_penalty,
        )
        content = (response.choices[0].message.content or "").strip()
        return CompletionResult(content=content, usage=CompletionUsage.from_any(response.usage))


class CustomHTTPChatClient(BaseClient):
    def __init__(self, api_key: str, base_url: str, rate_limiter: RateLimiter | None = None):
        super().__init__(api_key, rate_limiter)
        self.base_url = base_url.rstrip("/")

    def create_completion(self, config: ModelConfig, messages: list[dict[str, str]]) -> CompletionResult:
        self._acquire()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        data = {
            k: v
            for k, v in {
                "model": config.model,
                "messages": messages,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
                "top_p": config.top_p,
                "top_k": config.top_k,
                "repeat_penalty": config.repeat_penalty,
                "frequency_penalty": config.frequency_penalty,
                "presence_penalty": config.presence_penalty,
                "n": config.n,
                "stream": config.stream,
            }.items()
            if v is not None
        }
        for attempt in range(config.max_retries):
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=config.request_timeout_seconds,
                )
                response.raise_for_status()
                result = response.json()
                content = result["choices"][0]["message"]["content"].strip()
                usage = CompletionUsage.from_any(result.get("usage"))
                if usage.total_tokens == 0:
                    usage = self._estimated_usage(config, messages, content)
                return CompletionResult(content=content, usage=usage)
            except requests.exceptions.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 429 and attempt < config.max_retries - 1:
                    logger.warning("Rate limited by custom endpoint. Retrying in %.1fs", config.retry_delay_seconds)
                    time.sleep(config.retry_delay_seconds)
                    continue
                raise
        raise RuntimeError("Max retries exceeded")


class GeminiChatClient(BaseClient):
    def __init__(self, api_key: str, rate_limiter: RateLimiter | None = None):
        super().__init__(api_key, rate_limiter)
        import google.generativeai as genai

        self.genai: Any = genai
        self.genai.configure(api_key=api_key)

    def create_completion(self, config: ModelConfig, messages: list[dict[str, str]]) -> CompletionResult:
        from google.api_core import exceptions
        from google.generativeai.types import HarmBlockThreshold, HarmCategory

        self._acquire()
        generation_config = {
            "temperature": config.temperature,
            "top_p": config.top_p,
            "max_output_tokens": config.max_tokens,
        }
        if config.top_k is not None:
            generation_config["top_k"] = config.top_k

        model = self.genai.GenerativeModel(
            model_name=config.model,
            generation_config=generation_config,
            safety_settings={
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            },
        )
        history: list[dict[str, object]] = [
            {"role": item["role"], "parts": [item["content"]]} for item in messages[:-1]
        ]
        for attempt in range(config.max_retries):
            try:
                chat_session = model.start_chat(history=cast(Any, history))
                response = chat_session.send_message(messages[-1]["content"])
                content = response.text.strip()
                return CompletionResult(content=content, usage=self._estimated_usage(config, messages, content))
            except (exceptions.ResourceExhausted, Exception) as exc:
                if attempt < config.max_retries - 1:
                    logger.warning("Gemini API error: %s. Retrying in %.1fs", exc, config.retry_delay_seconds)
                    time.sleep(config.retry_delay_seconds)
                    continue
                raise
        raise RuntimeError("Max retries exceeded")
