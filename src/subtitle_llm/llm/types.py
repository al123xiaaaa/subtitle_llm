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
    # 厂商未提供时保持未知；推理用量是明细，不额外累加到 total_tokens。
    reasoning_tokens: int | None = None
    estimated: bool = False

    def add(self, other: "CompletionUsage") -> None:
        self.estimated = self.estimated or other.estimated
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.prompt_cache_hit_tokens += other.prompt_cache_hit_tokens
        self.prompt_cache_miss_tokens += other.prompt_cache_miss_tokens
        if other.reasoning_tokens is not None:
            self.reasoning_tokens = (self.reasoning_tokens or 0) + other.reasoning_tokens

    @classmethod
    def from_any(cls, usage) -> "CompletionUsage":
        if usage is None:
            return cls(estimated=True)
        def field(value, name):
            return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

        reasoning = field(field(usage, "completion_tokens_details"), "reasoning_tokens")
        if reasoning is None:
            reasoning = field(usage, "reasoning_tokens")
        return cls(
            prompt_tokens=int(field(usage, "prompt_tokens") or 0),
            completion_tokens=int(field(usage, "completion_tokens") or 0),
            total_tokens=int(field(usage, "total_tokens") or 0),
            prompt_cache_hit_tokens=int(field(usage, "prompt_cache_hit_tokens") or 0),
            prompt_cache_miss_tokens=int(field(usage, "prompt_cache_miss_tokens") or 0),
            reasoning_tokens=int(reasoning) if reasoning is not None else None,
            estimated=bool(field(usage, "estimated")) or not bool(field(usage, "total_tokens")),
        )

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "prompt_cache_hit_tokens": self.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": self.prompt_cache_miss_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            **({"estimated": True} if self.estimated else {}),
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


def output_budget_exhausted(model: str, max_tokens: int | None, *, truncated: bool) -> OutputBudgetExhaustedError:
    detail = "厂商报告输出额度耗尽，响应被截断" if truncated else "模型返回为空，可能是输出额度耗尽"
    budget = str(max_tokens) if max_tokens is not None else "未指定（使用厂商默认）"
    return OutputBudgetExhaustedError(
        f"{detail}（model={model}，max_tokens={budget}）。"
        "请检查厂商限制、停止原因和用量；可显式设置更大的 max_tokens 或减小翻译片段。"
    )


class ChatClient(Protocol):
    def create_completion(self, config: ModelConfig, messages: list[dict[str, str]]) -> CompletionResult:
        ...
