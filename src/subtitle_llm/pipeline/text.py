from __future__ import annotations

import re
from typing import Any

from subtitle_llm.domain import SubtitleEntry

STRONG_SENTENCE_ENDINGS = {".", "!", "?", "。", "！", "？", "…"}
TRAILING_CLOSERS = set("\"'”’)]}）】》」』〉")
LEADING_OPENERS = set("\"'“‘([{（【《「『〈")
CONTINUATION_WORDS = {
    "and",
    "but",
    "or",
    "so",
    "because",
    "that",
    "which",
    "who",
    "when",
    "while",
    "if",
    "to",
    "of",
    "with",
    "for",
    "as",
}


def is_sentence_complete(text: str) -> bool:
    stripped = text.strip()
    while stripped and stripped[-1] in TRAILING_CLOSERS:
        stripped = stripped[:-1].rstrip()
    return bool(stripped) and stripped[-1] in STRONG_SENTENCE_ENDINGS


def is_likely_continuation(text: str) -> bool:
    stripped = text.strip()
    while stripped and stripped[0] in LEADING_OPENERS:
        stripped = stripped[1:].lstrip()
    if not stripped:
        return False
    first_alpha = re.search(r"[A-Za-z]", stripped)
    if first_alpha and first_alpha.group(0).islower():
        return True
    first_word = re.match(r"[A-Za-z]+", stripped)
    return bool(first_word and first_word.group(0).lower() in CONTINUATION_WORDS)


def detect_boundary_risk(left_entry: SubtitleEntry, right_entry: SubtitleEntry) -> dict[str, Any] | None:
    reasons: list[str] = []
    if not is_sentence_complete(left_entry.original_text):
        reasons.append("previous entry does not end with strong sentence punctuation")
    if is_likely_continuation(right_entry.original_text):
        reasons.append("next entry looks like a sentence continuation")
    if not reasons:
        return None
    return {
        "before_index": left_entry.index,
        "after_index": right_entry.index,
        "reason": "; ".join(reasons),
    }


def format_boundary_entries(entries: list[SubtitleEntry]) -> str:
    if not entries:
        return "(none)"
    return "\n".join(f"[global {entry.index}]\n[{entry.original_text}]" for entry in entries)


def build_boundary_context(
    all_entries: list[SubtitleEntry],
    chunk: list[SubtitleEntry],
    window_size: int = 4,
) -> dict[str, Any]:
    if not all_entries or not chunk or window_size <= 0:
        return {"text": "No readonly boundary context.", "risks": []}

    index_to_position = {entry.index: i for i, entry in enumerate(all_entries)}
    positions = [index_to_position[entry.index] for entry in chunk if entry.index in index_to_position]
    if not positions:
        return {"text": "No readonly boundary context.", "risks": []}

    start = min(positions)
    end = max(positions)
    previous_entries = all_entries[max(0, start - window_size):start]
    next_entries = all_entries[end + 1:end + 1 + window_size]

    risks = []
    if start > 0:
        risk = detect_boundary_risk(all_entries[start - 1], all_entries[start])
        if risk:
            risks.append(risk)
    if end < len(all_entries) - 1:
        risk = detect_boundary_risk(all_entries[end], all_entries[end + 1])
        if risk:
            risks.append(risk)

    risk_text = "\n".join(
        f"- Boundary [{risk['before_index']}] -> [{risk['after_index']}]: {risk['reason']}"
        for risk in risks
    ) or "- No suspected cross-chunk sentence boundary."

    text = (
        "Readonly Boundary Context (do not translate or output these context entries):\n"
        "Previous context:\n"
        f"{format_boundary_entries(previous_entries)}\n\n"
        "Next context:\n"
        f"{format_boundary_entries(next_entries)}\n\n"
        "Boundary risk notes:\n"
        f"{risk_text}\n\n"
        "Use this readonly context only to understand sentence continuation, pronouns, tone, and terminology. "
        "Translate only the current chunk entries and never output readonly context entries."
    )
    return {"text": text, "risks": risks}


def chunk_list(entries: list[SubtitleEntry], chunk_size: int) -> list[list[SubtitleEntry]]:
    chunks: list[list[SubtitleEntry]] = []
    i = 0
    n = len(entries)
    sentence_endings = {".", "!", "?"}

    while i < n:
        end = min(i + chunk_size, n)
        split = end

        for j in range(end - 1, i - 1, -1):
            if any(entries[j].original_text.rstrip().endswith(punct) for punct in sentence_endings):
                split = j + 1
                break

        if split == end:
            extended_end = min(end + chunk_size, n)
            for j in range(end, extended_end):
                if any(entries[j].original_text.rstrip().endswith(punct) for punct in sentence_endings):
                    split = j + 1
                    break

        if split == i:
            split = end
        chunks.append(entries[i:split])
        i = split

    return chunks


def format_chunk(chunk: list[SubtitleEntry]) -> str:
    return "\n".join([f"[{i + 1}]\n[{entry.original_text}]" for i, entry in enumerate(chunk)])


def process_translation(original_text: str, translated_text: str, chunk: list[SubtitleEntry]) -> str:
    original_lines = original_text.split("\n")
    processed_lines: list[str] = []
    current_index: str | None = None
    current_translation = ""
    translations: dict[int, str] = {}

    for line in translated_text.splitlines():
        stripped_line = line.strip()
        if re.match(r"\[\d+\]", stripped_line):
            if current_index is not None and current_translation:
                processed_lines.append(f"[{current_index}]")
                processed_lines.append(current_translation.strip())
                translations[int(current_index)] = current_translation.strip()
            current_index = stripped_line.strip("[]")
            current_translation = ""
        else:
            current_translation += re.sub(r"^\[+|\]+$", "", line.strip()) + " "

    if current_index is not None and current_translation:
        processed_lines.append(f"[{current_index}]")
        processed_lines.append(current_translation.strip())
        translations[int(current_index)] = current_translation.strip()

    expected_entries = len(original_lines) // 2
    actual_entries = len(processed_lines) // 2
    while actual_entries < expected_entries:
        missing_index = actual_entries + 1
        processed_lines.append(f"[{missing_index}]")
        processed_lines.append(f"[Translation missing line - {missing_index}]")
        translations[missing_index] = f"[Translation missing line - {missing_index}]"
        actual_entries += 1

    for local_index, entry in enumerate(chunk, start=1):
        entry.translated_text = translations.get(local_index, "")

    return "\n".join(processed_lines)


def parse_translation_results(translation: str, chunk: list[SubtitleEntry]) -> list[tuple[SubtitleEntry, str]]:
    translated_lines = translation.strip().split("\n")
    results: list[tuple[SubtitleEntry, str]] = []
    for i, entry in enumerate(chunk):
        line_index = 2 * i + 1
        results.append((entry, translated_lines[line_index].strip() if line_index < len(translated_lines) else ""))
    return results


def combine_translations_by_index(original_translation: str, fixed_translation: str) -> str:
    original_lines = original_translation.strip().split("\n")
    fixed_lines = [line for line in fixed_translation.strip().split("\n") if line.strip()]
    fixed_translations: dict[str, str] = {}

    for i in range(0, len(fixed_lines), 2):
        if i + 1 < len(fixed_lines):
            fixed_translations[fixed_lines[i].strip("[]")] = fixed_lines[i + 1]

    combined_lines: list[str] = []
    for i in range(0, len(original_lines), 2):
        if i + 1 >= len(original_lines):
            continue
        index = original_lines[i].strip("[]")
        translation = original_lines[i + 1]
        if "Translation missing line" in translation and index in fixed_translations:
            translation = fixed_translations[index]
        combined_lines.append(f"[{index}]")
        combined_lines.append(translation)

    return "\n".join(combined_lines)


def extract_translation_block(content: str) -> str:
    match = re.search(r"<translation>(.*?)</translation>", content, re.DOTALL)
    return match.group(1).strip() if match else content.strip()
