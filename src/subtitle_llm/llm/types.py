from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from subtitle_llm.settings import ModelConfig


@dataclass
class CompletionUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # DeepSeek/OpenAI 等厂商的 prompt 缓存命中情况。命中部分计费更低（DeepSeek 0.1 元/百万，
    # 未命中 1 元/百万）。prompt_cache_hit_tokens 为命中缓存的输入 token，
    # prompt_cache_miss_tokens 为未命中的输入 token；二者之和约等于 prompt_tokens。
    # 非 DeepSeek 厂商（如 Gemini）不返回这两个字段，保持为 0。
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0

    def add(self, other: "CompletionUsage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.prompt_cache_hit_tokens += other.prompt_cache_hit_tokens
        self.prompt_cache_miss_tokens += other.prompt_cache_miss_tokens

    @classmethod
    def from_any(cls, usage) -> "CompletionUsage":
        if usage is None:
            return cls()
        if isinstance(usage, dict):
            return cls(
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                total_tokens=int(usage.get("total_tokens", 0) or 0),
                prompt_cache_hit_tokens=int(usage.get("prompt_cache_hit_tokens", 0) or 0),
                prompt_cache_miss_tokens=int(usage.get("prompt_cache_miss_tokens", 0) or 0),
            )
        return cls(
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
            prompt_cache_hit_tokens=int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0),
            prompt_cache_miss_tokens=int(getattr(usage, "prompt_cache_miss_tokens", 0) or 0),
        )

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "prompt_cache_hit_tokens": self.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": self.prompt_cache_miss_tokens,
        }


@dataclass
class CompletionResult:
    content: str
    usage: CompletionUsage
    finish_reason: str | None = None


class OutputBudgetExhaustedError(ValueError):
    """输出 token 额度耗尽：响应为空或被 max_tokens 截断。

    思考型模型（如 DeepSeek 思考模式）的思考过程计入输出额度，max_tokens
    太小时正文可能一个字都没有。此类错误重试无意义，应直接失败并提示用户。"""


def output_budget_exhausted(model: str, max_tokens: int, *, truncated: bool) -> OutputBudgetExhaustedError:
    detail = "输出被 max_tokens 截断" if truncated else "模型返回为空（思考过程可能耗尽输出额度）"
    return OutputBudgetExhaustedError(
        f"输出额度耗尽：{detail}（model={model}，max_tokens={max_tokens}）。"
        "请调大该模型的 max_tokens，或改用非思考型模型。"
    )


class ChatClient(Protocol):
    def create_completion(self, config: ModelConfig, messages: list[dict[str, str]]) -> CompletionResult:
        ...
