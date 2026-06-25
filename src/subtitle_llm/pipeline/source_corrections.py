from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from subtitle_llm.domain import SubtitleEntry

HARD_CORRECTION_TYPES = {"person", "place", "street", "inn", "organization", "institution"}


@dataclass(frozen=True)
class SourceCorrection:
    cue_ids: tuple[int, ...]
    observed: str
    corrected: str
    correction_type: str
    enforcement: str
    target_aliases: tuple[str, ...]
    confidence: str
    evidence: str = ""


@dataclass(frozen=True)
class SourceCorrectionFlag:
    cue_id: int
    observed: str
    corrected: str
    correction_type: str
    target_aliases: tuple[str, ...]
    current_translation: str
    evidence: str = ""

    def to_prompt_text(self) -> str:
        aliases = " / ".join(self.target_aliases) if self.target_aliases else "(none)"
        return (
            f"observed source: {self.observed}\n"
            f"corrected source: {self.corrected}\n"
            f"target aliases that should appear: {aliases}\n"
            f"evidence: {self.evidence or '(none)'}\n"
            f"current translation: {self.current_translation or '(empty)'}"
        )


def parse_source_corrections(items: Any) -> list[SourceCorrection]:
    if not isinstance(items, list):
        return []

    corrections: list[SourceCorrection] = []
    for item in items:
        if isinstance(item, dict):
            correction = source_correction_from_dict(item)
        else:
            correction = source_correction_from_text(str(item))
        if correction is not None:
            corrections.append(correction)
    return corrections


def source_correction_from_dict(item: dict[str, Any]) -> SourceCorrection | None:
    observed = str(item.get("observed", "")).strip()
    corrected = str(item.get("corrected", "")).strip()
    if not observed or not corrected:
        return None

    cue_ids: list[int] = []
    for value in item.get("cue_ids", []) or []:
        try:
            cue_ids.append(int(value))
        except (TypeError, ValueError):
            continue

    aliases = tuple(
        str(alias).strip()
        for alias in item.get("target_aliases", []) or []
        if str(alias).strip()
    )
    return SourceCorrection(
        cue_ids=tuple(cue_ids),
        observed=observed,
        corrected=corrected,
        correction_type=str(item.get("type", item.get("correction_type", "other"))).strip().lower() or "other",
        enforcement=str(item.get("enforcement", "soft")).strip().lower() or "soft",
        target_aliases=aliases,
        confidence=str(item.get("confidence", "medium")).strip().lower() or "medium",
        evidence=str(item.get("evidence", "")).strip(),
    )


def source_correction_from_text(value: str) -> SourceCorrection | None:
    text = value.strip().lstrip("-").strip()
    if not text or text.lower() in {"(none)", "none", "无", "无。"}:
        return None

    match = re.match(
        r"(?P<observed>.+?)\s*->\s*(?P<corrected>[^([]+)"
        r"(?:\((?P<alias>[^)]*)\))?"
        r"(?:\s*\[confidence:\s*(?P<confidence>high|medium|low)\])?",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    aliases = tuple(alias.strip() for alias in [match.group("alias") or ""] if alias.strip())
    return SourceCorrection(
        cue_ids=(),
        observed=match.group("observed").strip().strip('"'),
        corrected=match.group("corrected").strip().strip('"'),
        correction_type="other",
        enforcement="soft",
        target_aliases=aliases,
        confidence=(match.group("confidence") or "medium").lower(),
    )


def source_corrections_to_json(corrections: list[SourceCorrection]) -> str:
    return json.dumps([source_correction_to_dict(item) for item in corrections], ensure_ascii=False)


def source_correction_to_dict(item: SourceCorrection) -> dict[str, Any]:
    return {
        "cue_ids": list(item.cue_ids),
        "observed": item.observed,
        "corrected": item.corrected,
        "type": item.correction_type,
        "enforcement": item.enforcement,
        "target_aliases": list(item.target_aliases),
        "confidence": item.confidence,
        "evidence": item.evidence,
    }


def source_corrections_from_context(context: str) -> list[SourceCorrection]:
    match = re.search(
        r"Source corrections JSON:\s*(?P<json>\[[\s\S]*?\])\s*(?:\n[A-Z][^\n]*:|\Z)",
        context,
    )
    if not match:
        return []
    try:
        payload = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return []
    return parse_source_corrections(payload)


def find_unadopted_hard_corrections(
    corrections: list[SourceCorrection],
    entries: list[SubtitleEntry],
) -> list[SourceCorrectionFlag]:
    source_by_index = {entry.index: entry.original_text for entry in entries}
    translated_by_index = {entry.index: entry.translated_text for entry in entries}
    flags: list[SourceCorrectionFlag] = []

    for correction in corrections:
        if not should_enforce_hard(correction):
            continue
        cue_ids = locate_observed_cues(correction, source_by_index)
        for cue_id in cue_ids:
            current_translation = translated_by_index.get(cue_id, "")
            aliases = correction.target_aliases or (correction.corrected,)
            if translation_adopts_alias(current_translation, aliases):
                continue
            flags.append(
                SourceCorrectionFlag(
                    cue_id=cue_id,
                    observed=correction.observed,
                    corrected=correction.corrected,
                    correction_type=correction.correction_type,
                    target_aliases=aliases,
                    current_translation=current_translation,
                    evidence=correction.evidence,
                )
            )
    return flags


def should_enforce_hard(correction: SourceCorrection) -> bool:
    if correction.confidence not in {"high", "medium"}:
        return False
    if correction.enforcement != "hard":
        return False
    if same_without_case(correction.observed, correction.corrected):
        return False
    return correction.correction_type in HARD_CORRECTION_TYPES


def locate_observed_cues(correction: SourceCorrection, source_by_index: dict[int, str]) -> list[int]:
    needle = compact_ascii(correction.observed)
    if needle:
        matches = [index for index, text in source_by_index.items() if needle in compact_ascii(text)]
        if matches:
            return matches
    return [index for index in correction.cue_ids if index in source_by_index]


def translation_adopts_alias(text: str, aliases: tuple[str, ...]) -> bool:
    for alias in aliases:
        core = chinese_core(alias)
        if alias and alias in text:
            return True
        if core and len(core) >= 2 and core in text:
            return True
    return False


def chinese_core(alias: str) -> str:
    text = re.sub(r"[A-Za-z\s'’\".-]+", "", alias)
    for suffix in ("旅馆", "客栈", "酒店", "街道", "大街", "街", "路", "市场"):
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)]
    return text


def same_without_case(left: str, right: str) -> bool:
    return collapse_spaces(left).lower() == collapse_spaces(right).lower()


def collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def compact_ascii(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
