from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

try:
    import pysbd
except ImportError:  # pragma: no cover - dependency fallback for partially installed environments
    pysbd = None

from subtitle_llm.domain import Subtitle, SubtitleEntry


TimeMode = Literal["auto", "always", "off"]

SENTENCE_ENDINGS = {".", "!", "?"}
ABBREVIATIONS = {
    "dr",
    "mr",
    "mrs",
    "ms",
    "prof",
    "sr",
    "jr",
    "st",
    "vs",
    "etc",
    "e.g",
    "i.e",
}


@dataclass(frozen=True)
class NormalizationOptions:
    mode: TimeMode = "auto"
    min_overlap_ratio: float = 0.5
    min_unpunctuated_ratio: float = 0.65
    max_cue_chars: int = 84
    max_line_chars: int = 42
    max_duration_seconds: float = 7.0
    min_duration_seconds: float = 0.8
    gap_ms: int = 40
    sentence_language: str = "en"


@dataclass(frozen=True)
class NormalizedCueMap:
    normalized_index: int
    original_indices: list[int]
    original_start_index: int
    original_end_index: int
    start_time: str
    end_time: str
    text: str

    def to_dict(self) -> dict:
        return {
            "normalized_index": self.normalized_index,
            "original_indices": self.original_indices,
            "original_start_index": self.original_start_index,
            "original_end_index": self.original_end_index,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "text": self.text,
        }


@dataclass(frozen=True)
class NormalizationStats:
    original_entries: int
    normalized_entries: int
    overlap_pairs: int
    overlap_ratio: float
    unpunctuated_entries: int
    unpunctuated_ratio: float
    average_words_per_entry: float

    def to_dict(self) -> dict:
        return {
            "original_entries": self.original_entries,
            "normalized_entries": self.normalized_entries,
            "overlap_pairs": self.overlap_pairs,
            "overlap_ratio": round(self.overlap_ratio, 4),
            "unpunctuated_entries": self.unpunctuated_entries,
            "unpunctuated_ratio": round(self.unpunctuated_ratio, 4),
            "average_words_per_entry": round(self.average_words_per_entry, 2),
        }


@dataclass(frozen=True)
class NormalizationResult:
    subtitle: Subtitle
    applied: bool
    reason: str
    stats: NormalizationStats
    cue_map: list[NormalizedCueMap] = field(default_factory=list)

    def map_payload(self) -> dict:
        return {
            "applied": self.applied,
            "reason": self.reason,
            "stats": self.stats.to_dict(),
            "cues": [cue.to_dict() for cue in self.cue_map],
        }


@dataclass(frozen=True)
class TextSpan:
    entry: SubtitleEntry
    start: int
    end: int
    time_start_ms: int
    time_end_ms: int


@dataclass(frozen=True)
class SentenceSpan:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class CuePart:
    text: str
    start: int
    end: int


def normalize_subtitle(subtitle: Subtitle, options: NormalizationOptions | None = None) -> NormalizationResult:
    options = options or NormalizationOptions()
    stats = analyze_subtitle(subtitle, normalized_entries=len(subtitle.entries))
    if options.mode == "off":
        return NormalizationResult(subtitle=subtitle, applied=False, reason="normalization disabled", stats=stats)
    if not subtitle.entries:
        return NormalizationResult(subtitle=subtitle, applied=False, reason="empty subtitle", stats=stats)
    if options.mode == "auto" and not should_normalize(stats, options):
        return NormalizationResult(
            subtitle=subtitle,
            applied=False,
            reason="subtitle timing/text shape does not look like rolling captions",
            stats=stats,
        )

    transcript, spans = build_transcript(subtitle.entries)
    sentence_spans = split_sentences(transcript, language=options.sentence_language)
    if not sentence_spans:
        return NormalizationResult(subtitle=subtitle, applied=False, reason="no sentence spans detected", stats=stats)

    normalized_entries: list[SubtitleEntry] = []
    cue_map: list[NormalizedCueMap] = []
    for sentence in sentence_spans:
        source_entries = entries_for_span(sentence.start, sentence.end, spans)
        if not source_entries:
            continue
        source_start_ms = time_for_position(sentence.start, spans)
        source_end_ms = time_for_position(sentence.end, spans)
        if source_end_ms <= source_start_ms:
            source_end_ms = source_start_ms + int(options.min_duration_seconds * 1000)

        parts = split_cue_parts(sentence.text, options, source_end_ms - source_start_ms)
        for part in parts:
            cue_start_ms = time_for_position(sentence.start + part.start, spans)
            cue_end_ms = time_for_position(sentence.start + part.end, spans)
            cue_end_ms = max(cue_end_ms, cue_start_ms + int(options.min_duration_seconds * 1000))
            part_entries = (
                entries_for_span(sentence.start + part.start, sentence.start + part.end, spans)
                or source_entries
            )
            normalized_index = len(normalized_entries) + 1
            cue_text = wrap_cue_text(part.text, options.max_line_chars)
            normalized_entries.append(
                SubtitleEntry(
                    index=normalized_index,
                    start_time=ms_to_srt_time(cue_start_ms),
                    end_time=ms_to_srt_time(cue_end_ms),
                    original_text=cue_text,
                )
            )
            cue_map.append(
                NormalizedCueMap(
                    normalized_index=normalized_index,
                    original_indices=[entry.index for entry in part_entries],
                    original_start_index=part_entries[0].index,
                    original_end_index=part_entries[-1].index,
                    start_time=ms_to_srt_time(cue_start_ms),
                    end_time=ms_to_srt_time(cue_end_ms),
                    text=part.text,
                )
            )

    adjust_timings(normalized_entries, options)
    for entry, cue in zip(normalized_entries, cue_map, strict=False):
        cue_map[entry.index - 1] = NormalizedCueMap(
            normalized_index=entry.index,
            original_indices=cue.original_indices,
            original_start_index=cue.original_start_index,
            original_end_index=cue.original_end_index,
            start_time=entry.start_time,
            end_time=entry.end_time,
            text=cue.text,
        )

    normalized = Subtitle(normalized_entries)
    final_stats = analyze_subtitle(subtitle, normalized_entries=len(normalized.entries))
    return NormalizationResult(
        subtitle=normalized,
        applied=True,
        reason="rolling caption normalization applied",
        stats=final_stats,
        cue_map=cue_map,
    )


def write_normalization_map(result: NormalizationResult, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result.map_payload(), ensure_ascii=False, indent=2), encoding="utf-8")


def analyze_subtitle(subtitle: Subtitle, normalized_entries: int) -> NormalizationStats:
    entries = subtitle.entries
    total = len(entries)
    overlap_pairs = 0
    for left, right in zip(entries, entries[1:]):
        if srt_time_to_ms(left.end_time) > srt_time_to_ms(right.start_time):
            overlap_pairs += 1
    unpunctuated_entries = sum(1 for entry in entries if not ends_sentence(entry.original_text))
    total_words = sum(word_count(entry.original_text) for entry in entries)
    comparable_pairs = max(total - 1, 1)
    return NormalizationStats(
        original_entries=total,
        normalized_entries=normalized_entries,
        overlap_pairs=overlap_pairs,
        overlap_ratio=overlap_pairs / comparable_pairs if total > 1 else 0.0,
        unpunctuated_entries=unpunctuated_entries,
        unpunctuated_ratio=unpunctuated_entries / total if total else 0.0,
        average_words_per_entry=total_words / total if total else 0.0,
    )


def should_normalize(stats: NormalizationStats, options: NormalizationOptions) -> bool:
    if stats.original_entries < 3:
        return False
    return (
        stats.overlap_ratio >= options.min_overlap_ratio
        and stats.unpunctuated_ratio >= options.min_unpunctuated_ratio
    )


def build_transcript(entries: list[SubtitleEntry]) -> tuple[str, list[TextSpan]]:
    parts: list[str] = []
    spans: list[TextSpan] = []
    cursor = 0
    for index, entry in enumerate(entries):
        text = normalize_text(entry.original_text)
        if not text:
            continue
        if parts:
            parts.append(" ")
            cursor += 1
        start = cursor
        parts.append(text)
        cursor += len(text)
        time_start_ms, time_end_ms = entry_timing_window(entries, index)
        spans.append(
            TextSpan(
                entry=entry,
                start=start,
                end=cursor,
                time_start_ms=time_start_ms,
                time_end_ms=time_end_ms,
            )
        )
    return "".join(parts), spans


def entry_timing_window(entries: list[SubtitleEntry], index: int) -> tuple[int, int]:
    entry = entries[index]
    start_ms = srt_time_to_ms(entry.start_time)
    end_ms = srt_time_to_ms(entry.end_time)
    if index + 1 < len(entries):
        next_start_ms = srt_time_to_ms(entries[index + 1].start_time)
        if start_ms < next_start_ms < end_ms:
            end_ms = next_start_ms
    if end_ms <= start_ms:
        end_ms = start_ms + 1
    return start_ms, end_ms


def time_for_position(position: int, spans: list[TextSpan]) -> int:
    if not spans:
        return 0
    for span in spans:
        if span.start <= position <= span.end:
            if span.end <= span.start:
                return span.time_start_ms
            ratio = (position - span.start) / (span.end - span.start)
            return span.time_start_ms + round((span.time_end_ms - span.time_start_ms) * ratio)
    if position < spans[0].start:
        return spans[0].time_start_ms
    return spans[-1].time_end_ms


def split_sentences(text: str, language: str = "en") -> list[SentenceSpan]:
    text = normalize_text(text)
    if not text:
        return []
    pysbd_spans = split_sentences_with_pysbd(text, language)
    if pysbd_spans:
        return pysbd_spans
    return split_sentences_with_fallback_rules(text)


def split_sentences_with_pysbd(text: str, language: str) -> list[SentenceSpan]:
    language_code = pysbd_language_code(language)
    if not language_code or pysbd is None:
        return []
    try:
        segments = pysbd.Segmenter(language=language_code, clean=False).segment(text)
    except Exception:
        return []

    spans: list[SentenceSpan] = []
    cursor = 0
    for segment in segments:
        sentence = normalize_text(segment)
        if not sentence:
            continue
        start = text.find(sentence, cursor)
        if start < 0:
            return []
        end = start + len(sentence)
        spans.append(SentenceSpan(sentence, start, end))
        cursor = end
    return spans


def pysbd_language_code(language: str) -> str | None:
    normalized = language.strip().lower()
    if normalized in {"en", "eng", "english"}:
        return "en"
    return None


def split_sentences_with_fallback_rules(text: str) -> list[SentenceSpan]:
    boundaries: list[int] = []
    for index, char in enumerate(text):
        if char not in SENTENCE_ENDINGS:
            continue
        if is_abbreviation_at(text, index):
            continue
        if is_decimal_point_at(text, index):
            continue
        boundaries.append(index + 1)

    spans: list[SentenceSpan] = []
    start = 0
    for end in boundaries:
        sentence = text[start:end].strip()
        if sentence:
            leading = len(text[start:end]) - len(text[start:end].lstrip())
            trailing = len(text[start:end].rstrip())
            spans.append(SentenceSpan(sentence, start + leading, start + trailing))
        start = end
    remainder = text[start:].strip()
    if remainder:
        leading = len(text[start:]) - len(text[start:].lstrip())
        spans.append(SentenceSpan(remainder, start + leading, len(text)))
    return spans


def split_cue_parts(text: str, options: NormalizationOptions, duration_ms: int) -> list[CuePart]:
    text = normalize_text(text)
    if not text:
        return []
    part_count = max(1, math.ceil(duration_ms / max(1, int(options.max_duration_seconds * 1000))))
    target_chars = options.max_cue_chars
    if part_count > 1:
        target_chars = min(target_chars, max(options.max_line_chars, math.ceil(len(text) / part_count)))
    if len(text) <= target_chars:
        return [CuePart(text=text, start=0, end=len(text))]

    words = list(re.finditer(r"\S+", text))
    if not words:
        return [CuePart(text=text, start=0, end=len(text))]

    parts: list[CuePart] = []
    group_start = 0
    for index, word in enumerate(words):
        current = text[words[group_start].start() : word.end()].strip()
        if len(current) <= target_chars or group_start == index:
            continue
        previous = words[index - 1]
        parts.append(
            CuePart(
                text=text[words[group_start].start() : previous.end()].strip(),
                start=words[group_start].start(),
                end=previous.end(),
            )
        )
        group_start = index
    parts.append(
        CuePart(
            text=text[words[group_start].start() : words[-1].end()].strip(),
            start=words[group_start].start(),
            end=words[-1].end(),
        )
    )
    return parts


def entries_for_span(start: int, end: int, spans: list[TextSpan]) -> list[SubtitleEntry]:
    return [span.entry for span in spans if span.start < end and span.end > start]


def adjust_timings(entries: list[SubtitleEntry], options: NormalizationOptions) -> None:
    min_duration_ms = int(options.min_duration_seconds * 1000)
    previous_end_ms: int | None = None
    for entry in entries:
        start_ms = srt_time_to_ms(entry.start_time)
        end_ms = max(srt_time_to_ms(entry.end_time), start_ms + min_duration_ms)
        if previous_end_ms is not None:
            earliest_start_ms = previous_end_ms + options.gap_ms
            if start_ms < earliest_start_ms:
                start_ms = earliest_start_ms
            end_ms = max(end_ms, start_ms + min_duration_ms)
        entry.start_time = ms_to_srt_time(start_ms)
        entry.end_time = ms_to_srt_time(end_ms)
        previous_end_ms = end_ms


def wrap_cue_text(text: str, max_line_chars: int) -> str:
    text = normalize_text(text)
    if len(text) <= max_line_chars:
        return text
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_line_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def ends_sentence(text: str) -> bool:
    return normalize_text(text).rstrip().endswith(tuple(SENTENCE_ENDINGS))


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def is_abbreviation_at(text: str, period_index: int) -> bool:
    prefix = text[:period_index].rstrip()
    match = re.search(r"([A-Za-z](?:[A-Za-z.]?[A-Za-z])?)$", prefix)
    if not match:
        return False
    token = match.group(1).lower().rstrip(".")
    if token in ABBREVIATIONS:
        return True
    return len(token) == 1 and token.isalpha()


def is_decimal_point_at(text: str, period_index: int) -> bool:
    if text[period_index] != ".":
        return False
    previous_char = text[period_index - 1] if period_index > 0 else ""
    next_char = text[period_index + 1] if period_index + 1 < len(text) else ""
    return previous_char.isdigit() and next_char.isdigit()


def srt_time_to_ms(value: str) -> int:
    hours, minutes, seconds_ms = value.split(":")
    seconds, millis = seconds_ms.split(",")
    return (
        int(hours) * 60 * 60 * 1000
        + int(minutes) * 60 * 1000
        + int(seconds) * 1000
        + int(millis)
    )


def ms_to_srt_time(value: int) -> str:
    value = max(0, value)
    hours, remainder = divmod(value, 60 * 60 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"
