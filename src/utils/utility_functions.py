import re
import math
from typing import List, Dict, Any
from src.models.subtitle_entry import SubtitleEntry


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


def process_translation(
    original_text: str, translated_text: str, chunk: List[SubtitleEntry]
) -> str:
    """Process the translated text to match the desired format with indices and fill chunks."""
    original_lines = original_text.split("\n")
    translated_lines = translated_text.split("\n")
    processed_lines = []
    current_index = None
    current_translation = ""
    translations_dict = {}

    for line in translated_lines:
        stripped_line = line.strip()
        # Check if the line is an index, e.g., [1]
        if re.match(r"\[\d+\]", stripped_line):
            # If there's an existing translation, append it before starting a new one
            if current_index is not None and current_translation:
                processed_lines.append(f"[{current_index}]")
                processed_lines.append(f"{current_translation.strip()}  ")
                translations_dict[int(current_index)] = current_translation.strip()
            # Extract the new index
            current_index = stripped_line.strip("[]")
            current_translation = ""
        else:
            line = re.sub(r"^\[+|\]+$", "", line.strip())
            current_translation += line + " "

    # Append the last translation if exists
    if current_index is not None and current_translation:
        processed_lines.append(f"[{current_index}]")
        processed_lines.append(f"{current_translation.strip()}  ")
        translations_dict[int(current_index)] = current_translation.strip()

    # Ensure the number of translated entries matches the original entries
    expected_entries = (
        len(original_lines) // 2
    )  # Each entry has two lines: index and text
    actual_entries = len(processed_lines) // 2

    while actual_entries < expected_entries:
        missing_index = actual_entries + 1
        processed_lines.append(f"[{missing_index}]")
        processed_lines.append(f"[Translation missing line - {missing_index}]")
        translations_dict[missing_index] = (
            f"[Translation missing line - {missing_index}]"
        )
        actual_entries += 1

    # 使用局部索引（1..chunk_size）来填充 translated_text
    for local_idx, entry in enumerate(chunk, start=1):
        entry.translated_text = translations_dict.get(local_idx, "")

    return "\n".join(processed_lines)


def combine_translations_by_index(original_translation, fixed_translation):
    # Split both translations into lines
    original_lines = original_translation.strip().split("\n")
    fixed_lines = [
        line for line in fixed_translation.strip().split("\n") if line.strip()
    ]

    # Create a dictionary to store the fixed translations
    fixed_translations = {}
    for i in range(0, len(fixed_lines), 2):
        index = fixed_lines[i].strip("[]")
        translation = fixed_lines[i + 1]
        fixed_translations[index] = translation

    # Combine the translations
    combined_lines = []
    for i in range(0, len(original_lines), 2):
        index = original_lines[i].strip("[]")
        translation = original_lines[i + 1]

        # If the line was missing and has been fixed, use the fixed translation
        if "Translation missing line" in translation and index in fixed_translations:
            translation = fixed_translations[index]

        combined_lines.append(f"[{index}]")
        combined_lines.append(translation)

    # Join the combined lines into a single string
    return "\n".join(combined_lines)


def chunk_list(lst: List[SubtitleEntry], chunk_size: int) -> List[List[SubtitleEntry]]:
    """
    Split a list of SubtitleEntry into chunks, trying to respect sentence boundaries.

    :param lst: List of SubtitleEntry objects to be chunked.
    :param chunk_size: Approximate number of lines per chunk.
    :return: A list of chunks, each being a list of SubtitleEntry objects.
    """
    chunks = []
    i = 0
    n = len(lst)
    sentence_endings = {".", "!", "?"}

    while i < n:
        # Tentative end index for the current chunk
        end = min(i + chunk_size, n)
        split = end

        # Search for the last entry within the chunk that ends with a sentence-ending punctuation
        for j in range(end - 1, i - 1, -1):
            if any(
                lst[j].original_text.rstrip().endswith(punct)
                for punct in sentence_endings
            ):
                split = j + 1
                break

        # If no sentence boundary found within the chunk, try to extend the chunk
        if split == end:
            # Look ahead up to another chunk_size for a sentence boundary
            extended_end = min(end + chunk_size, n)
            for j in range(end, extended_end):
                if any(
                    lst[j].original_text.rstrip().endswith(punct)
                    for punct in sentence_endings
                ):
                    split = j + 1
                    break

        # If still no sentence boundary found, split at the original end
        if split == i:
            split = end

        # Append the current chunk
        chunks.append(lst[i:split])
        i = split

    return chunks


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
