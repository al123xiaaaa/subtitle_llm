from __future__ import annotations

from subtitle_llm.llm.clients import CustomHTTPChatClient, GeminiChatClient, OpenAIChatClient
from subtitle_llm.llm.rate_limiter import RateLimiter
from subtitle_llm.llm.types import ChatClient
from subtitle_llm.settings import ModelConfig, ModelProvider


def create_chat_client(config: ModelConfig) -> ChatClient:
    api_key = config.require_api_key()
    rate_limiter = RateLimiter(config.rate_limit) if config.rate_limit else None

    if config.provider == ModelProvider.OPENAI:
        return OpenAIChatClient(api_key=api_key, base_url=config.endpoint, rate_limiter=rate_limiter)
    if config.provider == ModelProvider.CUSTOM:
        if not config.endpoint:
            raise ValueError("custom provider requires endpoint")
        return CustomHTTPChatClient(api_key=api_key, base_url=config.endpoint, rate_limiter=rate_limiter)
    if config.provider == ModelProvider.GEMINI:
        return GeminiChatClient(api_key=api_key, rate_limiter=rate_limiter)
    raise ValueError(f"Unsupported provider: {config.provider}")
