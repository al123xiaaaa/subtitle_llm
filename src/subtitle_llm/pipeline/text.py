from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from subtitle_llm.domain import SubtitleEntry

if TYPE_CHECKING:
    import tiktoken

STRONG_SENTENCE_ENDINGS = {".", "!", "?", "。", "！", "？", "…"}
TRAILING_CLOSERS = set("\"'”’)]}）】》」』〉")


def parse_translation_json(content: str) -> Any:
    """解析翻译响应里的 JSON，标准解析失败时用 json-repair 兜底挽救。

    LLM 偶尔会吐出语法有瑕疵的 JSON：未闭合的括号、被截断的数组、把思考过程混进
    字段值等。标准 ``json.loads`` 一遇到这些就整体失败、整片回退原文。这里在标准
    解析失败后，用 ``json_repair`` 尝试修复再解析，把「数据其实都在、只是壳坏了」
    的响应救回来；救不回来的仍抛出原异常，交由上层重试或回退。
    """
    payload = extract_json_payload(content)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as strict_error:
        try:
            from json_repair import repair_json

            repaired = repair_json(payload, return_objects=False)
            return json.loads(repaired)
        except Exception:
            raise strict_error
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


def chunk_list(
    entries: list[SubtitleEntry],
    chunk_size: int,
    *,
    max_output_tokens: int | None = None,
    encoder: tiktoken.Encoding | None = None,
) -> list[list[SubtitleEntry]]:
    """按条目数与（可选的）输出 token 预算切分字幕。

    - ``chunk_size``：每个块的条目数上限（始终生效）。
    - ``max_output_tokens``：每块的输出 token 预算。命中即切，避免翻译输出超过
      ``max_tokens`` 被截断。需与 ``encoder`` 同时提供；不传则只按条目数切（向后兼容）。

    两条约束取严格者。块内仍优先落在句末标点（在两约束都未超的窗口内）；
    若 token 预算先到则按预算切（截断防护优先于断句美观）。
    """
    chunks: list[list[SubtitleEntry]] = []
    i = 0
    n = len(entries)
    sentence_endings = {".", "!", "?"}
    budget_active = max_output_tokens is not None and encoder is not None

    while i < n:
        end = min(i + chunk_size, n)
        split = end

        # token 预算约束：在 [i, end) 内找不超预算的最远切点。
        if budget_active:
            assert max_output_tokens is not None and encoder is not None
            budget_end = i
            for j in range(i, end):
                candidate_texts = [entries[k].original_text for k in range(i, j + 1)]
                if estimate_chunk_output_tokens(candidate_texts, encoder) > max_output_tokens:
                    break
                budget_end = j + 1
            # 至少保留 1 条，避免零长块死循环。
            budget_end = max(budget_end, i + 1)
            if budget_end < end:
                end = budget_end
                split = end

        # 句子边界对齐：在 [i, end) 内优先落在句末标点。
        for j in range(end - 1, i - 1, -1):
            if any(entries[j].original_text.rstrip().endswith(punct) for punct in sentence_endings):
                split = j + 1
                break

        if split == end and not budget_active:
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


def estimate_chunk_output_tokens(source_texts: list[str], encoder: tiktoken.Encoding) -> int:
    """估算一批源文本翻译后的输出 token（膨胀系数 + 每条结构开销）。"""
    from subtitle_llm.llm.token_counter import estimate_output_tokens

    return estimate_output_tokens(source_texts, encoder)


def format_chunk(chunk: list[SubtitleEntry]) -> str:
    return "\n".join([f"[{i + 1}]\n[{entry.original_text}]" for i, entry in enumerate(chunk)])


def format_semantic_units_json(chunk: list[SubtitleEntry]) -> str:
    data = [
        {
            "unit_id": index,
            "source": entry.original_text,
        }
        for index, entry in enumerate(chunk, start=1)
    ]
    return json.dumps(data, ensure_ascii=False, indent=2)


def format_translation_reference(entries: list[SubtitleEntry]) -> str:
    if not entries:
        return "(none)"
    return "\n".join(
        f"[{i + 1}] global {entry.index}\n"
        f"Original: {entry.original_text}\n"
        f"Previous translation: {entry.translated_text or '(empty)'}"
        for i, entry in enumerate(entries)
    )


def format_alignment_anchors(entries: list[SubtitleEntry]) -> str:
    if not entries:
        return "(none)"
    return "\n".join(
        f"[global {entry.index}]\n"
        f"Original: {entry.original_text}\n"
        f"Confirmed translation: {entry.translated_text or '(empty)'}"
        for entry in entries
    )


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


def parse_indexed_translation_strict(translation: str, expected_count: int) -> list[str]:
    translations = parse_indexed_translation_map(translation)
    return indexed_map_to_ordered_list(translations, list(range(1, expected_count + 1)))


def parse_indexed_translation_for_entries(translation: str, entries: list[SubtitleEntry]) -> list[str]:
    try:
        return parse_indexed_translation_strict(translation, len(entries))
    except ValueError as local_error:
        translations = parse_indexed_translation_map(translation)
        global_indices = [entry.index for entry in entries]
        try:
            return indexed_map_to_ordered_list(translations, global_indices)
        except ValueError:
            raise local_error


def parse_indexed_translation_map(translation: str) -> dict[int, list[str]]:
    translations: dict[int, list[str]] = {}
    current_index: int | None = None

    for line in translation.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^\[(\d+)\]\s*(.*)$", stripped)
        if match:
            current_index = int(match.group(1))
            if current_index in translations:
                raise ValueError(f"duplicate translation index: {current_index}")
            translations[current_index] = []
            inline_text = match.group(2).strip()
            if inline_text:
                translations[current_index].append(inline_text)
            continue
        if current_index is None:
            raise ValueError("translation text appeared before the first [index] marker")
        translations[current_index].append(stripped)
    return translations


def indexed_map_to_ordered_list(translations: dict[int, list[str]], expected_order: list[int]) -> list[str]:
    expected = set(expected_order)
    observed = set(translations)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ValueError(f"translation index mismatch: missing={missing}, extra={extra}")

    parsed: list[str] = []
    for index in expected_order:
        text = " ".join(translations[index]).strip()
        if not text:
            raise ValueError(f"translation index {index} is empty")
        parsed.append(text)
    return parsed


def format_indexed_translations(translations: list[str]) -> str:
    lines: list[str] = []
    for index, translation in enumerate(translations, start=1):
        lines.append(f"[{index}]")
        lines.append(translation.strip())
    return "\n".join(lines)


def process_semantic_json_translation(content: str, chunk: list[SubtitleEntry]) -> str:
    data = parse_translation_json(content)
    rows = data.get("translations") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("semantic translation JSON must contain a translations list")

    translations: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("semantic translation item must be an object")
        unit_id = int(row["unit_id"])
        translation = str(row.get("translation", "")).strip()
        if unit_id in translations:
            raise ValueError(f"duplicate semantic unit_id: {unit_id}")
        translations[unit_id] = translation

    expected = set(range(1, len(chunk) + 1))
    observed = set(translations)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ValueError(f"semantic translation unit_id mismatch: missing={missing}, extra={extra}")

    lines: list[str] = []
    for index, entry in enumerate(chunk, start=1):
        translated = translations[index].strip()
        entry.set_translated_text(translated)
        lines.append(f"[{index}]")
        lines.append(translated)
    return "\n".join(lines)


def process_timed_cue_json_translation(content: str, entries: list[SubtitleEntry]) -> str:
    data = parse_translation_json(content)
    rows = data.get("translations") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("timed cue translation JSON must contain a translations list")

    translations: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("timed cue translation item must be an object")
        cue_id_value = row.get("cue_id", row.get("index", row.get("source_index")))
        if cue_id_value is None:
            raise ValueError("timed cue translation item must contain cue_id")
        cue_id = int(cue_id_value)
        translation = str(row.get("translation", "")).strip()
        if cue_id in translations:
            raise ValueError(f"duplicate timed cue_id: {cue_id}")
        translations[cue_id] = translation

    local_ids = list(range(1, len(entries) + 1))
    local_id_set = set(local_ids)
    global_ids = [entry.index for entry in entries]
    global_id_set = set(global_ids)
    observed = set(translations)

    if observed == local_id_set:
        ordered_ids = local_ids
    elif observed == global_id_set:
        ordered_ids = global_ids
    else:
        missing_local = sorted(local_id_set - observed)
        extra_local = sorted(observed - local_id_set)
        missing_global = sorted(global_id_set - observed)
        extra_global = sorted(observed - global_id_set)
        raise ValueError(
            "timed cue translation id mismatch: "
            f"local_missing={missing_local}, local_extra={extra_local}, "
            f"global_missing={missing_global}, global_extra={extra_global}"
        )

    lines: list[str] = []
    for local_index, (entry, cue_id) in enumerate(zip(entries, ordered_ids, strict=True), start=1):
        translated = translations[cue_id].strip()
        if not translated:
            raise ValueError(f"timed cue translation {cue_id} is empty")
        entry.set_translated_text(translated)
        lines.append(f"[{local_index}]")
        lines.append(translated)
    return "\n".join(lines)


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


def extract_json_payload(content: str) -> str:
    stripped = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return stripped
    start_candidates = [index for index in [stripped.find("{"), stripped.find("[")] if index >= 0]
    if not start_candidates:
        raise ValueError("no JSON object or array found in semantic translation response")
    start = min(start_candidates)
    opener = stripped[start]
    closer = "}" if opener == "{" else "]"
    end = stripped.rfind(closer)
    if end < start:
        raise ValueError("semantic translation response contains incomplete JSON")
    return stripped[start:end + 1]
