#!/usr/bin/env python3
"""PROTOTYPE: source-correction gate for cue-aware subtitle translation.

Question this throwaway script answers:
Can a structured source-interpretation pass produce hard corrections, map them
back to source cues, detect whether the current translation adopted them, and
repair only the affected cue?

This is intentionally outside src/subtitle_llm. If the idea is accepted, fold
the useful parts into ContextService, QualityGate, and semantic cue repair.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from subtitle_llm.io import SubtitleIO  # noqa: E402
from subtitle_llm.llm.factory import create_chat_client  # noqa: E402
from subtitle_llm.settings import load_config  # noqa: E402

DEFAULT_SOURCE = ROOT / (
    "data/input/DutchKafir 🇳🇱 - Mijns inziens is dit een van de beste use cases "
    "voor AI-videocontent ....srt"
)
DEFAULT_TRANSLATION = ROOT / (
    "data/output/DutchKafir 🇳🇱 - Mijns inziens is dit een van de beste use cases "
    "voor AI-videocontent ....zh.srt"
)
DEFAULT_CONFIG = ROOT / "data/desktop-configs/latest-model-config.yaml"

HARD_TYPES = {"person", "place", "street", "inn", "organization", "institution"}


def main() -> int:
    parser = argparse.ArgumentParser(description="PROTOTYPE source-correction gate.")
    parser.add_argument("--source-srt", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--translated-srt", type=Path, default=DEFAULT_TRANSLATION)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--repair", action="store_true", help="Run one local LLM repair for each hard flag.")
    args = parser.parse_args()

    source_entries = SubtitleIO.read_srt(args.source_srt).entries
    source_by_index = {entry.index: entry.original_text for entry in source_entries}
    translated_by_index = parse_source_first_translations(args.translated_srt)

    config = load_config(args.config)
    audit = run_source_audit(config, source_entries)
    corrections = audit["payload"].get("source_corrections", [])
    hard_flags = find_unadopted_hard_corrections(corrections, source_by_index, translated_by_index)

    output: dict[str, Any] = {
        "prototype": "source-correction-gate",
        "source_entries": len(source_entries),
        "translated_entries": len(translated_by_index),
        "audit_usage": audit["usage"],
        "correction_count": len(corrections),
        "corrections": corrections,
        "hard_flag_count": len(hard_flags),
        "hard_flags": hard_flags,
    }

    if args.repair and hard_flags:
        repairs = []
        for flag in hard_flags:
            repairs.append(run_local_repair(config, flag, source_by_index, translated_by_index))
        output["repairs"] = repairs
        output["repair_usage_total"] = add_usage(item["usage"] for item in repairs)

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def run_source_audit(config, source_entries) -> dict[str, Any]:
    source_rows = [f"{entry.index}. {entry.original_text}" for entry in source_entries]
    prompt = (
        "Audit ASR subtitle source text. Find likely source corrections that matter for translation.\n"
        "Targets: historical people, places, streets, inns, institutions, period terms, homophones, "
        "common words that become named entities from nearby evidence.\n"
        "Output compact STRICT JSON only:\n"
        '{"source_corrections":[{"cue_ids":[0],"observed":"","corrected":"","type":'
        '"person|place|street|inn|organization|institution|period_term|common_term",'
        '"enforcement":"hard|soft","target_aliases":[""],"confidence":"high|medium|low",'
        '"evidence":"short grounded reason"}]}\n'
        "Rules: max 8 corrections; no summary; no markdown. Use enforcement=hard only for named "
        "entities or historically anchored proper nouns. target_aliases must include likely Chinese "
        "renderings and useful source proper nouns.\n\n"
        "Subtitles:\n"
        + "\n".join(source_rows)
    )
    model = config.summary_model.model_copy(update={"temperature": 0.2})
    client = create_chat_client(model)
    result = client.create_completion(
        model,
        [
            {"role": "system", "content": "You output compact strict JSON only."},
            {"role": "user", "content": prompt},
        ],
    )
    return {
        "usage": result.usage.to_dict(),
        "payload": extract_json_object(result.content),
    }


def find_unadopted_hard_corrections(
    corrections: list[dict[str, Any]],
    source_by_index: dict[int, str],
    translated_by_index: dict[int, str],
) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    for item in corrections:
        if str(item.get("confidence")) not in {"high", "medium"}:
            continue
        if same_without_case(str(item.get("observed", "")), str(item.get("corrected", ""))):
            continue
        if not is_hard_correction(item):
            continue

        cue_ids = locate_observed_cues(str(item.get("observed", "")), item.get("cue_ids", []), source_by_index)
        current_translation = "\n".join(translated_by_index.get(cue_id, "") for cue_id in cue_ids)
        aliases = [str(alias).strip() for alias in item.get("target_aliases", []) if str(alias).strip()]

        if not translation_adopts_alias(current_translation, aliases):
            flags.append(
                {
                    "cue_ids": cue_ids,
                    "observed": item.get("observed"),
                    "corrected": item.get("corrected"),
                    "type": item.get("type"),
                    "target_aliases": aliases,
                    "current_translation": current_translation,
                    "evidence": item.get("evidence"),
                }
            )
    return flags


def is_hard_correction(item: dict[str, Any]) -> bool:
    if str(item.get("enforcement")) != "hard":
        return False
    correction_type = str(item.get("type", ""))
    if correction_type in HARD_TYPES:
        return True
    evidence = str(item.get("evidence", "")).lower()
    aliases = " ".join(str(alias) for alias in item.get("target_aliases", []))
    return any(marker in evidence for marker in ("street", "inn", "king", "reign", "market street")) or any(
        marker in aliases for marker in ("街", "亨利", "旅馆", "客栈")
    )


def locate_observed_cues(observed: str, fallback_ids: list[Any], source_by_index: dict[int, str]) -> list[int]:
    needle = compact_ascii(observed)
    fallback = [int(value) for value in fallback_ids if str(value).strip().isdigit()]
    if not needle:
        return fallback
    matches = [index for index, text in source_by_index.items() if needle in compact_ascii(text)]
    return matches or fallback


def translation_adopts_alias(text: str, aliases: list[str]) -> bool:
    for alias in aliases:
        core = chinese_core(alias)
        if alias in text:
            return True
        if core and len(core) >= 2 and core in text:
            return True
    return False


def run_local_repair(
    config,
    flag: dict[str, Any],
    source_by_index: dict[int, str],
    translated_by_index: dict[int, str],
) -> dict[str, Any]:
    cue_id = int(flag["cue_ids"][0])
    nearby_ids = [index for index in range(max(1, cue_id - 1), cue_id + 2) if index in source_by_index]
    nearby = "\n".join(
        f"{index}. SRC: {source_by_index[index]}\n   ZH: {translated_by_index.get(index, '')}"
        for index in nearby_ids
    )
    aliases = " / ".join(flag["target_aliases"])
    prompt = (
        f"Repair only cue {cue_id}. Keep subtitle timing segmentation and output STRICT JSON only.\n"
        "Preserve all non-erroneous meaning from the source cue, including discourse markers such as "
        "'so', 'okay', or fillers when they are present.\n"
        f"Correction to enforce: observed source {json.dumps(flag['observed'], ensure_ascii=False)} means "
        f"{json.dumps(flag['corrected'], ensure_ascii=False)}. Chinese should contain one of: {aliases}.\n"
        f"Nearby cues:\n{nearby}\n"
        f'Return: {{"cue_id":{cue_id},"translation":"..."}}'
    )
    model = config.translation_model.model_copy(update={"temperature": 0.2, "max_tokens": 800})
    client = create_chat_client(model)
    result = client.create_completion(
        model,
        [
            {"role": "system", "content": "You are a subtitle repair translator. Output strict JSON only."},
            {"role": "user", "content": prompt},
        ],
    )
    return {
        "flag": flag,
        "usage": result.usage.to_dict(),
        "response": extract_json_object(result.content),
    }


def parse_source_first_translations(path: Path) -> dict[int, str]:
    text = path.read_text(encoding="utf-8")
    translations: dict[int, str] = {}
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) >= 4 and lines[0].isdigit() and "-->" in lines[1]:
            translations[int(lines[0])] = lines[-1]
    return translations


def extract_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"No JSON object in response: {content[:200]!r}")
    return json.loads(text[start : end + 1])


def same_without_case(left: str, right: str) -> bool:
    return collapse_spaces(left).lower() == collapse_spaces(right).lower()


def collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def compact_ascii(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def chinese_core(alias: str) -> str:
    text = re.sub(r"[A-Za-z\s'’\".-]+", "", alias)
    for suffix in ("旅馆", "客栈", "酒店", "街道", "大街", "街", "路", "市场"):
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)]
    return text


def add_usage(usages) -> dict[str, int]:
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for usage in usages:
        for key in total:
            total[key] += int(usage.get(key, 0) or 0)
    return total


if __name__ == "__main__":
    raise SystemExit(main())
