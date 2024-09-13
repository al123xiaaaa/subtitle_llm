import re
import math
from typing import List, Dict, Any


def seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT time format (HH:MM:SS,mmm)"""
    millis = int(round((seconds - math.floor(seconds)) * 1000))
    seconds = math.floor(seconds)
    mins, sec = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    return f"{hrs:02}:{mins:02}:{sec:02},{millis:03}"


def count_words(text: str) -> int:
    """Count the number of words in a text, excluding standalone punctuation."""
    words = re.findall(r"\b\w+\b", text)
    return len(words)


def process_translation(original_text: str, translated_text: str) -> str:
    """Process the translated text to match the original format."""
    original_lines = original_text.split("\n")
    translated_lines = translated_text.split("\n")
    processed_lines = []
    current_translation = ""

    for line in translated_lines:
        if line.strip().startswith("[") and line.strip().endswith("]"):
            if current_translation:
                processed_lines.append(current_translation.strip())
            current_translation = ""
        else:
            current_translation += line + " "

    if current_translation:
        processed_lines.append(current_translation.strip())

    # 确保翻译后的行数与原始行数相同
    while (
        len(processed_lines) < len(original_lines) // 2
    ):  # 因为原始文本每两行表示一个条目
        # 对于缺失的翻译行，添加占位符
        processed_lines.append(
            f"[Translation missing line - {len(processed_lines) + 1}]"
        )

    return "\n".join(processed_lines)


def chunk_list(lst: List[Any], chunk_size: int) -> List[List[Any]]:
    """Split a list into chunks of specified size."""
    return [lst[i : i + chunk_size] for i in range(0, len(lst), chunk_size)]


def load_yaml_config() -> Dict[str, Any]:
    """Load YAML configuration file."""
    import os
    import yaml

    # 获取当前脚本的目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 上一级目录为 src
    src_dir = os.path.dirname(current_dir)
    # 构建 config.yaml 的路径
    config_path = os.path.join(src_dir, "config", "config.yaml")
    with open(config_path, "r") as file:
        return yaml.safe_load(file)
