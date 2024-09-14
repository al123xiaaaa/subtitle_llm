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
    """Process the translated text to match the desired format with indices."""
    original_lines = original_text.split("\n")
    translated_lines = translated_text.split("\n")
    processed_lines = []
    current_index = None
    current_translation = ""

    for line in translated_lines:
        stripped_line = line.strip()
        # Check if the line is an index, e.g., [1]
        if re.match(r"\[\d+\]", stripped_line):
            # If there's an existing translation, append it before starting a new one
            if current_index is not None and current_translation:
                processed_lines.append(f"[{current_index}]")
                processed_lines.append(f"{current_translation.strip()}  ")
            # Extract the new index
            current_index = stripped_line.strip("[]")
            current_translation = ""
        else:
            current_translation += line + " "

    # Append the last translation if exists
    if current_index is not None and current_translation:
        processed_lines.append(f"[{current_index}]")
        processed_lines.append(f"{current_translation.strip()}  ")

    # Ensure the number of translated entries matches the original entries
    expected_entries = (
        len(original_lines) // 2
    )  # Each entry has two lines: index and text
    actual_entries = len(processed_lines) // 2

    while actual_entries < expected_entries:
        missing_index = actual_entries + 1
        processed_lines.append(f"[{missing_index}]")
        processed_lines.append(f"[Translation missing line - {missing_index}]")
        actual_entries += 1

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
