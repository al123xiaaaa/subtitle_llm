from __future__ import annotations

import re
from dataclasses import dataclass

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.pipeline.semantic_layout import split_translation_by_layout_contract
from subtitle_llm.pipeline.text import is_sentence_complete


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


def semantic_unit_source_entries(units: list[SemanticUnit]) -> list[SubtitleEntry]:
    return [entry for unit in units for entry in unit.entries]


def format_semantic_timed_cues_json(units: list[SemanticUnit]) -> str:
    cue_id = 0
    data = []
    for unit in units:
        cue_rows = []
        for entry in unit.entries:
            cue_id += 1
            cue_rows.append({
                "cue_id": cue_id,
                "source_index": entry.index,
                "time": f"{entry.start_time} --> {entry.end_time}",
                "source": entry.original_text,
            })
        data.append({
            "unit_id": unit.index,
            "source": unit.source_text,
            "cues": cue_rows,
        })
    return json_dumps(data)


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

    return split_translation_by_layout_contract(
        cleaned,
        entries,
        target_language=target_language,
    )


def normalize_spaces(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text.strip())


def json_dumps(data: object) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, indent=2)
