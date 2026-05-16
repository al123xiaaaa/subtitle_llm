from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from subtitle_llm.settings import ModelConfig


@dataclass
class CompletionUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "CompletionUsage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens

    @classmethod
    def from_any(cls, usage) -> "CompletionUsage":
        if usage is None:
            return cls()
        if isinstance(usage, dict):
            return cls(
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                total_tokens=int(usage.get("total_tokens", 0) or 0),
            )
        return cls(
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        )

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class CompletionResult:
    content: str
    usage: CompletionUsage


class ChatClient(Protocol):
    def create_completion(self, config: ModelConfig, messages: list[dict[str, str]]) -> CompletionResult:
        ...
