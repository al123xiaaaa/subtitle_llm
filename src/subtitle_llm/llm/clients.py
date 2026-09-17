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
        budget_params: dict[str, Any] = {}
        if config.max_tokens is not None:
            budget_params["max_tokens"] = config.max_tokens
        response = self.client.chat.completions.create(
            model=config.model,
            messages=cast(Any, messages),
            **budget_params,
            temperature=config.temperature,
            top_p=config.top_p,
            frequency_penalty=config.frequency_penalty,
            presence_penalty=config.presence_penalty,
        )
        content = (response.choices[0].message.content or "").strip()
        finish_reason = response.choices[0].finish_reason
        return CompletionResult(
            content=content,
            usage=CompletionUsage.from_any(response.usage),
            finish_reason=str(finish_reason) if finish_reason else None,
        )


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
                choice = result["choices"][0]
                content = (choice["message"]["content"] or "").strip()
                usage = CompletionUsage.from_any(result.get("usage"))
                if usage.total_tokens == 0:
                    estimated = self._estimated_usage(config, messages, content)
                    estimated.reasoning_tokens = usage.reasoning_tokens
                    usage = estimated
                finish_reason = choice.get("finish_reason")
                return CompletionResult(
                    content=content,
                    usage=usage,
                    finish_reason=str(finish_reason) if finish_reason else None,
                )
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

    @staticmethod
    def _finish_reason(reason: Any) -> str | None:
        # SDK 的 FinishReason 是 IntEnum；保留未知原因名称，避免模型名称推断。
        name = getattr(reason, "name", None)
        if not name or name == "FINISH_REASON_UNSPECIFIED":
            return None
        if name == "MAX_TOKENS":
            return "length"
        if name == "STOP":
            return "stop"
        if name in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}:
            return "content_filter"
        return name.lower()

    def create_completion(self, config: ModelConfig, messages: list[dict[str, str]]) -> CompletionResult:
        from google.api_core import exceptions
        from google.generativeai.types import HarmBlockThreshold, HarmCategory

        self._acquire()
        generation_config = {
            "temperature": config.temperature,
            "top_p": config.top_p,
        }
        if config.max_tokens is not None:
            generation_config["max_output_tokens"] = config.max_tokens
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
                candidates = response.candidates
                candidate = candidates[0] if candidates else None
                # 不访问 response.text：被截断/拦截且没有正文时 SDK 会抛错。
                # 仅取正文 part，绝不把厂商的思考文本写入译文或 trace。
                content = "".join(
                    part.text for part in candidate.content.parts
                    if not getattr(part, "thought", False)
                ).strip() if candidate is not None else ""
                finish_reason = self._finish_reason(candidate.finish_reason) if candidate is not None else None
                metadata = getattr(response, "usage_metadata", None)
                if metadata is not None:
                    reasoning = getattr(metadata, "thoughts_token_count", None)
                    usage = CompletionUsage(
                        prompt_tokens=metadata.prompt_token_count,
                        completion_tokens=metadata.candidates_token_count,
                        total_tokens=metadata.total_token_count,
                        reasoning_tokens=int(reasoning) if reasoning is not None else None,
                    )
                else:
                    usage = self._estimated_usage(config, messages, content)
                return CompletionResult(content=content, usage=usage, finish_reason=finish_reason)
            except (exceptions.ResourceExhausted, Exception) as exc:
                if attempt < config.max_retries - 1:
                    logger.warning("Gemini API error: %s. Retrying in %.1fs", exc, config.retry_delay_seconds)
                    time.sleep(config.retry_delay_seconds)
                    continue
                raise
        raise RuntimeError("Max retries exceeded")
