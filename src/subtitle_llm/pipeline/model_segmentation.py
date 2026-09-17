"""模型决定切点；程序只维护源文覆盖、位置和时间映射。

位置编号不是字幕边界。英文按词、无空格文字按字提供可引用的位置，
字幕数量及切点都由模型返回；绝不根据字符阈值再拆分模型的结果。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.pipeline.normalization import entry_timing_window, ms_to_srt_time, normalize_text, wrap_cue_text
from subtitle_llm.pipeline.source_corrections import source_corrections_from_context
from subtitle_llm.pipeline.chunks import ChunkPlanner, PlannedChunk
from subtitle_llm.pipeline.semantic_units import build_semantic_units, semantic_entries
from subtitle_llm.settings import PipelineConfig

if TYPE_CHECKING:
    from tiktoken import Encoding

_CJK = "\\u3400-\\u9fff\\u3040-\\u30ff\\uac00-\\ud7af"
_ATOMS = re.compile(f"[{_CJK}]|[^\\s{_CJK}]+")


def plan_model_chunks(
    entries: list[SubtitleEntry], options: PipelineConfig, max_output_tokens: int | None, encoder: Encoding,
) -> list[PlannedChunk]:
    """按完整语义组装请求，底层 ASR 文本只决定预算，不预先决定字幕切点。"""
    units = build_semantic_units(entries, options.semantic_max_cues_per_unit)
    by_index = {unit.index: unit for unit in units}
    chunks = ChunkPlanner(
        options.chunk_size, options.context_window_size, 0,
        max_output_tokens=max_output_tokens, encoder=encoder,
        output_text_resolver=lambda entry: [cue.original_text for cue in by_index[entry.index].entries],
    ).plan(semantic_entries(units))
    for chunk in chunks:
        chunk.entries = [cue for entry in chunk.entries for cue in by_index[entry.index].entries]
    return chunks


@dataclass(frozen=True)
class SourcePosition:
    index: int
    source_index: int
    text: str
    start: int
    end: int
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class Piece:
    start: int
    end: int
    text: str

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "text": self.text}


class SegmentationSource:
    def __init__(self, entries: list[SubtitleEntry]):
        self.positions: list[SourcePosition] = []
        self.by_entry: dict[int, list[SourcePosition]] = {}
        texts: list[str] = []
        cursor = 0
        for i, entry in enumerate(entries):
            text = normalize_text(entry.original_text)
            if not text:
                continue
            if texts:
                cursor += 1
            texts.append(text)
            start_ms, end_ms = entry_timing_window(entries, i)
            positions = []
            for match in _ATOMS.finditer(text):
                position = SourcePosition(
                    len(self.positions) + 1, entry.index, match.group(),
                    cursor + match.start(), cursor + match.end(),
                    start_ms + round((end_ms - start_ms) * match.start() / len(text)),
                    start_ms + round((end_ms - start_ms) * match.end() / len(text)),
                )
                self.positions.append(position)
                positions.append(position)
            self.by_entry[entry.index] = positions
            cursor += len(text)
        self.text = " ".join(texts)

    def bounds(self, entries: list[SubtitleEntry]) -> tuple[int, int]:
        positions = [p for e in entries for p in self.by_entry.get(e.index, [])]
        if not positions:
            raise ValueError("翻译片段没有可定位的源文")
        return positions[0].index, positions[-1].index

    def source_text(self, start: int, end: int) -> str:
        return self.text[self.positions[start - 1].start:self.positions[end - 1].end]

    def format_range(self, start: int, end: int, *, local_positions: bool = True) -> str:
        """原文只出现一次；每个 ASR 段只附一个紧凑的相对时间窗口。"""
        selected = self.positions[start - 1:end]
        origin = selected[0].start_ms
        offset = start - 1 if local_positions else 0
        groups: list[list[SourcePosition]] = []
        for position in selected:
            if not groups or groups[-1][-1].source_index != position.source_index:
                groups.append([])
            groups[-1].append(position)
        return "\n".join(
            f"({(group[0].start_ms - origin) / 1000:.2f}-{(group[-1].end_ms - origin) / 1000:.2f}s) "
            + " ".join(f"{p.index - offset}:{p.text}" for p in group)
            for group in groups
        )

    def to_cues(self, pieces: list[Piece], options: PipelineConfig) -> list[SubtitleEntry]:
        cues = []
        for piece in sorted(pieces, key=lambda item: item.start):
            first, last = self.positions[piece.start - 1], self.positions[piece.end - 1]
            if last.end_ms <= first.start_ms:
                raise ValueError(f"源文时间窗口无效：{piece.start}-{piece.end}")
            cues.append(SubtitleEntry(
                # 内部编号取首个源文位置，跨并发、局部重试和任务恢复保持稳定。
                index=piece.start,
                start_time=ms_to_srt_time(first.start_ms),
                end_time=ms_to_srt_time(last.end_ms),
                original_text=wrap_cue_text(self.source_text(piece.start, piece.end), options.normalize_max_line_chars),
                translated_text=piece.text,
            ))
        return cues

    def fallback_cues(self, entries: list[SubtitleEntry]) -> list[SubtitleEntry]:
        return [SubtitleEntry(
            index=self.by_entry[e.index][0].index,
            start_time=ms_to_srt_time(self.by_entry[e.index][0].start_ms),
            end_time=ms_to_srt_time(self.by_entry[e.index][-1].end_ms),
            original_text=normalize_text(e.original_text),
            needs_retranslation=True,
        ) for e in entries if self.by_entry.get(e.index)]


def compact_context(context: str, source: str) -> str:
    """保留全局摘要，筛选本片段术语/纠错；证据及重复摘要留在本地。"""
    if "Overall summary:" not in context or "Short Terms:" not in context:
        # 用户自定义上下文的结构未知，不能静默裁剪。
        return context
    summary = context.split("Overall summary:", 1)[1].split("\nShort Terms:", 1)[0].strip()
    terms_line = context.split("Short Terms:", 1)[1].split("\n", 1)[0]
    terms = [term.strip() for term in re.split(r",\s*(?![^()]*\))", terms_line)]
    source_key = normalize_text(source).casefold()
    relevant = [term for term in terms if term.split("(", 1)[0].strip().casefold() in source_key]
    corrections = []
    for item in source_corrections_from_context(context):
        if any(normalize_text(value).casefold() in source_key for value in (item.observed, item.corrected) if value):
            corrections.append({"source": item.observed, "use": item.corrected,
                                "aliases": list(item.target_aliases), "enforcement": item.enforcement})
    return json.dumps({"summary": summary, "terms": relevant, "corrections": corrections},
                      ensure_ascii=False, separators=(",", ":"))


def context_without_cue_ids(context: str) -> str:
    """断句后旧 cue 编号已失效，专名检查必须按原文定位，不能误命中新编号。"""
    pattern = r"(Source corrections JSON:\s*)(\[[\s\S]*?\])(?=\s*(?:\n[A-Z][^\n]*:|\Z))"

    def replace_ids(match: re.Match) -> str:
        try:
            rows = json.loads(match.group(2))
        except json.JSONDecodeError:
            return match.group()
        if not isinstance(rows, list):
            return match.group()
        return match.group(1) + json.dumps(
            [{**row, "cue_ids": []} if isinstance(row, dict) else row for row in rows], ensure_ascii=False,
        )

    return re.sub(pattern, replace_ids, context)


def segmentation_prompt(
    source: SegmentationSource, start: int, end: int, context: str,
    target_language: str, options: PipelineConfig, *, repair: bool = False,
    boundary_context: str = "",
    legacy: bool = False,
) -> str:
    # 前后各最多 24 个源文位置，只读且不带可输出的编号。
    before = source.source_text(max(1, start - 24), start - 1) if start > 1 else ""
    after = source.source_text(end + 1, min(len(source.positions), end + 24)) if end < len(source.positions) else ""
    relevant_source = " ".join((before, source.source_text(start, end), after))
    # v1 提示仅用于精确查找旧任务缓存；新的生成/补齐请求一律使用局部编号。
    first, last = (start, end) if legacy else (1, end - start + 1)
    instructions = f"""Translate subtitles into {target_language} and choose natural readable cue boundaries in ONE pass.
Keep meaningful phrases together. Avoid leaving a dependent one- or two-word tail on its own.
Keep names and technical phrases (such as GPU memory) together whenever possible.
Aim for {options.normalize_min_duration:g}-{options.normalize_max_duration:g} seconds per cue,
at most {options.normalize_max_cue_chars} source characters and readable translated lines.
Use the approximate source timing windows to judge pacing; do not cross long pauses unnecessarily.
These are readability goals, not a requirement to fill each cue to its limit. Preserve independent short utterances.
Preserve ALL source meaning and apply relevant terminology/corrections in the translation.
Positions identify source words/characters; the displayed ASR groups are NOT required cue boundaries.
""" if legacy else f"""Create bilingual timed subtitles in {target_language}: choose source boundaries and translate each range together.
Each text must translate ONLY its own source range, from the previous end + 1 through its end (inclusive).
Never move meaning, names, numbers or modifiers into an earlier/later cue; do not translate the whole passage and then distribute it.
Choose a self-contained phrase or clause that reads naturally in BOTH languages. Reorder words only WITHIN that range.
If natural target word order needs the neighboring clause, include both in ONE cue and translate the combined range.
Keep complete names, noun/verb phrases and idioms together; leave no fragment of the next phrase at the preceding cue's end.
Aim for {options.normalize_min_duration:g}-{options.normalize_max_duration:g} seconds and at most {options.normalize_max_cue_chars} source characters per cue.
These are soft goals: prefer a slightly longer coherent cue to a broken phrase. Do not make many tiny cues or split just to fill a quota.
Use approximate timing to allow enough reading time for BOTH languages; keep translations concise without losing meaning.
Join brief transitions with their related clause when appropriate; preserve truly independent short utterances and long pauses.
Apply relevant terminology/corrections. Use context only to disambiguate, never to add statements, conclusions or previews.
ASR line breaks are arbitrary: a phrase may continue on the next line; do not split it at the line break.
Before returning, silently check each text against its exact source range and check both sides of every boundary.
"""
    return instructions + f"""Return a JSON array only: [{{"end": <source position>, "text": "translation"}}, ...].
Cover positions {first} through {last} exactly once, in order. First cue starts at {first};
each later cue starts immediately after the preceding end. Ends must strictly increase; last end MUST be {last}.
Copy position numbers from the input. Do not count words, repeat source text, output timestamps or explanations.
Every text must be a nonempty translation, not punctuation alone.
{"Repair ONLY this incomplete range; surrounding cues have already been accepted." if repair else ""}

Context: {compact_context(context, relevant_source)}
Readonly before: {before}
Readonly after: {after}
{boundary_context}
Source positions {first}-{last}:
{source.format_range(start, end, local_positions=not legacy)}
"""


def response_rows(content: str) -> list:
    """截断响应仅抢救完整 JSON 对象，不猜测未返回的文本或位置。"""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        if not content.startswith("["):
            return []
        rows = []
        decoder = json.JSONDecoder()
        cursor = 1
        while cursor < len(content):
            while cursor < len(content) and content[cursor] in " \r\n\t,":
                cursor += 1
            try:
                row, cursor = decoder.raw_decode(content, cursor)
            except json.JSONDecodeError:
                break
            rows.append(row)
        return rows
    return data if isinstance(data, list) else []


def parse_pieces(content: str, start: int, end: int) -> list[Piece]:
    pieces = []
    cursor = start
    uncertain = False
    for row in response_rows(content):
        boundary = row.get("end") if isinstance(row, dict) else None
        if type(boundary) is not int or not cursor <= boundary <= end:
            uncertain = True
            continue
        text = row.get("text")
        if not uncertain and isinstance(text, str) and text.strip() and any(c.isalnum() for c in text):
            pieces.append(Piece(cursor, boundary, text.strip()))
        # 位置错误使下一条的起点也不可信：把两条一起留给局部修复。
        uncertain = False
        cursor = boundary + 1
    return pieces


def missing_ranges(pieces: list[Piece], start: int, end: int) -> list[tuple[int, int]]:
    missing = []
    cursor = start
    for piece in sorted(pieces, key=lambda item: item.start):
        if not cursor <= piece.start <= piece.end <= end:
            raise ValueError("模型断句范围重复、乱序或越界")
        if piece.start > cursor:
            missing.append((cursor, piece.start - 1))
        cursor = piece.end + 1
    if cursor <= end:
        missing.append((cursor, end))
    return missing
