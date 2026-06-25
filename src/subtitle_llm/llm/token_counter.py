from __future__ import annotations

import tiktoken

# 翻译输出相对源文的保守膨胀系数（英→中实际更短，但 CJK 每字约 1 token，留足余量防截断）。
_DEFAULT_EXPANSION_FACTOR = 2.0
# 每条 cue 的 JSON 结构开销，如 {"cue_id": N, "translation": "..."} 的固定 token 成本。
_PER_ENTRY_OVERHEAD_TOKENS = 12


def build_token_encoder(model: str) -> "tiktoken.Encoding":
    """按模型名构建 tiktoken encoding，未知模型回退到 cl100k_base。"""
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def estimate_output_tokens(
    source_texts: list[str],
    encoder: "tiktoken.Encoding",
    *,
    expansion_factor: float = _DEFAULT_EXPANSION_FACTOR,
) -> int:
    """估算把一批源文本翻译后的输出 token 数。

    用于 chunk 规划：避免单个 chunk 的翻译输出超过 max_tokens 而被截断。
    估算 = 源文 token × 膨胀系数 + 每条固定开销。
    """
    if not source_texts:
        return 0
    source_tokens = sum(len(encoder.encode(text)) for text in source_texts if text)
    entry_overhead = len(source_texts) * _PER_ENTRY_OVERHEAD_TOKENS
    return int(source_tokens * expansion_factor) + entry_overhead


def num_tokens_from_messages(messages: list[dict[str, str]], model: str) -> int:
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    if model.startswith("gpt-3.5-turbo"):
        tokens_per_message = 4
        tokens_per_name = -1
    else:
        tokens_per_message = 3
        tokens_per_name = 1

    count = 0
    for message in messages:
        count += tokens_per_message
        for key, value in message.items():
            count += len(encoding.encode(str(value)))
            if key == "name":
                count += tokens_per_name
    return count + 3
