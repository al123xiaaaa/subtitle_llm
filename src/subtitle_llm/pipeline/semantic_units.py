from __future__ import annotations

import re
from dataclasses import dataclass

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.pipeline.text import is_sentence_complete


CJK_PUNCTUATION = set("，。！？；：、,.!?;:")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class SemanticUnit:
    index: int
    entries: list[SubtitleEntry]
    source_text: str

    def to_entry(self) -> SubtitleEntry:
        return SubtitleEntry(
            index=self.index,
            start_time=self.entries[0].start_time,
            end_time=self.entries[-1].end_time,
            original_text=self.source_text,
        )

    @property
    def cue_indices(self) -> list[int]:
        return [entry.index for entry in self.entries]


def build_semantic_units(entries: list[SubtitleEntry], max_cues_per_unit: int = 6) -> list[SemanticUnit]:
    units: list[SemanticUnit] = []
    pending: list[SubtitleEntry] = []

    for entry in entries:
        pending.append(entry)
        pending_text = join_source_text(pending)
        if is_sentence_complete(pending_text) or len(pending) >= max_cues_per_unit:
            units.append(make_unit(len(units) + 1, pending))
            pending = []

    if pending:
        units.append(make_unit(len(units) + 1, pending))

    return units


def make_unit(index: int, entries: list[SubtitleEntry]) -> SemanticUnit:
    return SemanticUnit(index=index, entries=list(entries), source_text=join_source_text(entries))


def join_source_text(entries: list[SubtitleEntry]) -> str:
    return normalize_spaces(" ".join(entry.original_text.replace("\n", " ") for entry in entries))


def semantic_entries(units: list[SemanticUnit]) -> list[SubtitleEntry]:
    return [unit.to_entry() for unit in units]


def apply_semantic_translation(
    unit: SemanticUnit,
    translated_text: str,
    *,
    target_language: str,
    write_back_from_index: int | None = None,
) -> list[SubtitleEntry]:
    pieces = split_translation(translated_text, unit.entries, target_language=target_language)
    for entry, piece in zip(unit.entries, pieces, strict=True):
        if write_back_from_index is not None and entry.index < write_back_from_index:
            continue
        entry.set_translated_text(piece)
        entry.needs_retranslation = False
    return unit.entries


def split_translation(
    translated_text: str,
    entries: list[SubtitleEntry],
    *,
    target_language: str,
) -> list[str]:
    cleaned = normalize_spaces(translated_text)
    if len(entries) <= 1:
        return [cleaned]
    if not cleaned:
        return ["" for _ in entries]

    weights = [max(1, len(normalize_spaces(entry.original_text))) for entry in entries]
    if is_cjk_target(target_language):
        return split_cjk_text(cleaned, weights)
    return split_word_text(cleaned, weights)


def split_cjk_text(text: str, weights: list[int]) -> list[str]:
    boundaries = weighted_boundaries(len(text), weights)
    pieces: list[str] = []
    start = 0
    for boundary in boundaries:
        split_at = choose_cjk_split(text, start, boundary)
        pieces.append(text[start:split_at].strip())
        start = split_at
    pieces.append(text[start:].strip())
    return rebalance_empty_pieces(pieces)


def choose_cjk_split(text: str, start: int, desired: int) -> int:
    minimum = max(start + 1, desired - 8)
    maximum = min(len(text) - 1, desired + 8)
    best = max(start + 1, min(desired, len(text) - 1))
    best_score = abs(best - desired)
    for index in range(minimum, maximum + 1):
        score = abs(index - desired)
        if text[index - 1] in CJK_PUNCTUATION:
            score -= 4
        if index < len(text) and text[index] in CJK_PUNCTUATION:
            score -= 2
        if score < best_score:
            best = index
            best_score = score
    return best


def split_word_text(text: str, weights: list[int]) -> list[str]:
    words = text.split()
    if len(words) < len(weights):
        return split_cjk_text(text, weights)

    boundaries = weighted_boundaries(len(words), weights)
    pieces: list[str] = []
    start = 0
    for boundary in boundaries:
        split_at = max(start + 1, min(boundary, len(words) - 1))
        pieces.append(" ".join(words[start:split_at]).strip())
        start = split_at
    pieces.append(" ".join(words[start:]).strip())
    return rebalance_empty_pieces(pieces)


def weighted_boundaries(total_length: int, weights: list[int]) -> list[int]:
    total_weight = sum(weights)
    consumed = 0
    boundaries: list[int] = []
    for weight in weights[:-1]:
        consumed += weight
        boundary = round(total_length * consumed / total_weight)
        boundaries.append(max(1, min(boundary, total_length - 1)))
    for index in range(1, len(boundaries)):
        if boundaries[index] <= boundaries[index - 1]:
            boundaries[index] = min(total_length - 1, boundaries[index - 1] + 1)
    return boundaries


def rebalance_empty_pieces(pieces: list[str]) -> list[str]:
    if all(pieces):
        return pieces
    combined = "".join(pieces)
    if not combined:
        return pieces
    if len(combined) < len(pieces):
        return [combined if index == 0 else "" for index, _ in enumerate(pieces)]
    balanced: list[str] = []
    start = 0
    for index in range(len(pieces)):
        remaining_slots = len(pieces) - index
        remaining_chars = len(combined) - start
        take = max(1, remaining_chars // remaining_slots)
        if index == len(pieces) - 1:
            take = remaining_chars
        balanced.append(combined[start:start + take])
        start += take
    return balanced


def is_cjk_target(target_language: str) -> bool:
    normalized = target_language.strip().lower()
    return (
        normalized in {"zh", "zh-cn", "zh_cn", "ja", "jp", "ko"}
        or "chinese" in normalized
        or "中文" in normalized
        or "japanese" in normalized
        or "korean" in normalized
    )


def normalize_spaces(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text.strip())
