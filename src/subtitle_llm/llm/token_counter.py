from __future__ import annotations

import tiktoken


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
