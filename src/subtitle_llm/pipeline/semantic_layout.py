from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

from subtitle_llm.domain import SubtitleEntry


LayoutAction = Literal["auto_merge", "review", "quality"]

BOUNDARY_PUNCTUATION = set("，。！？；：、,.!?;:")
SENTENCE_PUNCTUATION = set("。！？.!?")
ORPHAN_PUNCTUATION = set("，。！？；：、,.!?;:“”‘’\"'）】》」』)]}>…—-")
CLOSING_PUNCTUATION = set("，。！？；：、,.!?;:“”‘’\"'）】》」』)]}>…—-")
OPEN_TO_CLOSE = {
    "(": ")",
    "（": "）",
    "[": "]",
    "【": "】",
    "{": "}",
    "《": "》",
    "“": "”",
    "‘": "’",
    "\"": "\"",
    "'": "'",
}
LATIN_TOKEN_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9_./:+#-]*[A-Za-z0-9])?"
LATIN_TOKEN_RE = re.compile(LATIN_TOKEN_PATTERN)
LATIN_TOKEN_SEQUENCE_RE = re.compile(rf"{LATIN_TOKEN_PATTERN}(?:\s+{LATIN_TOKEN_PATTERN})+")
LATIN_CONNECTOR_SEQUENCE_RE = re.compile(
    rf"{LATIN_TOKEN_PATTERN}(?:\s*(?:或|和|与|及|/)\s*{LATIN_TOKEN_PATTERN})+"
    rf"(?:\s+{LATIN_TOKEN_PATTERN})*"
)
WHITESPACE_RE = re.compile(r"\s+")
TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")


@dataclass(frozen=True)
class ProtectedSpan:
    start: int
    end: int
    reason: str


@dataclass(frozen=True)
class LayoutIssue:
    issue_type: str
    action: LayoutAction
    left_index: int | None
    right_index: int
    reason: str


def split_translation_by_layout_contract(
    translated_text: str,
    entries: list[SubtitleEntry],
    *,
    target_language: str,
) -> list[str]:
    del target_language
    text = normalize_spaces(translated_text)
    if len(entries) <= 1:
        return [text]
    if not text:
        return ["" for _ in entries]

    weights = semantic_weights(entries)
    spans = protected_spans(text)
    return split_text_by_layout_contract(text, weights, spans)


def split_text_by_layout_contract(text: str, weights: list[float], spans: list[ProtectedSpan]) -> list[str]:
    if len(weights) <= 1:
        return [text]

    boundaries = weighted_boundaries(len(text), weights)
    candidate_layers = [
        boundary_candidates(text, desired, len(weights), spans)
        for desired in boundaries
    ]
    chosen = choose_boundaries_with_dp(text, boundaries, candidate_layers, spans)
    if chosen is None:
        chosen = choose_boundaries_greedily(text, boundaries, spans)

    pieces: list[str] = []
    start = 0
    for boundary in chosen:
        pieces.append(text[start:boundary].strip())
        start = boundary
    pieces.append(text[start:].strip())
    return rebalance_empty_pieces(pieces)


def choose_boundaries_with_dp(
    text: str,
    targets: list[int],
    candidate_layers: list[list[int]],
    spans: list[ProtectedSpan],
) -> list[int] | None:
    total_pieces = len(targets) + 1
    states: dict[int, tuple[float, list[int]]] = {0: (0.0, [])}
    text_length = len(text)

    for layer_index, candidates in enumerate(candidate_layers):
        next_states: dict[int, tuple[float, list[int]]] = {}
        remaining_pieces = total_pieces - layer_index - 1
        target = targets[layer_index]
        for candidate in candidates:
            if text_length - candidate < remaining_pieces:
                continue
            for previous, (previous_cost, previous_path) in states.items():
                if candidate <= previous:
                    continue
                segment = text[previous:candidate].strip()
                if not segment:
                    continue
                cost = (
                    previous_cost
                    + boundary_cost(text, candidate, target, spans)
                    + segment_cost(segment)
                )
                current = next_states.get(candidate)
                if current is None or cost < current[0]:
                    next_states[candidate] = (cost, [*previous_path, candidate])
        if not next_states:
            return None
        states = next_states

    best_path: list[int] | None = None
    best_cost = math.inf
    for previous, (previous_cost, path) in states.items():
        final_segment = text[previous:].strip()
        if not final_segment:
            continue
        cost = previous_cost + segment_cost(final_segment)
        if cost < best_cost:
            best_cost = cost
            best_path = path
    return best_path


def choose_boundaries_greedily(text: str, targets: list[int], spans: list[ProtectedSpan]) -> list[int]:
    boundaries: list[int] = []
    previous = 0
    for target in targets:
        candidates = [
            position
            for position in range(previous + 1, len(text))
            if boundary_allowed(text, position, spans)
        ]
        if not candidates:
            boundary = max(previous + 1, min(target, len(text) - 1))
        else:
            boundary = min(candidates, key=lambda position: abs(position - target))
        boundaries.append(boundary)
        previous = boundary
    return boundaries


def boundary_candidates(
    text: str,
    desired: int,
    piece_count: int,
    spans: list[ProtectedSpan],
) -> list[int]:
    text_length = len(text)
    window = max(12, text_length // max(3, piece_count))
    candidates = {
        position
        for position in range(max(1, desired - window), min(text_length, desired + window + 1))
    }
    for position in range(1, text_length):
        previous = text[position - 1]
        current = text[position]
        if previous in BOUNDARY_PUNCTUATION or previous.isspace() or current.isspace():
            candidates.add(position)
        if is_latin_char(previous) != is_latin_char(current):
            candidates.add(position)

    valid = [
        position
        for position in sorted(candidates)
        if boundary_allowed(text, position, spans)
    ]
    if valid:
        return valid
    return [
        position
        for position in range(1, text_length)
        if boundary_allowed(text, position, spans)
    ]


def boundary_allowed(text: str, position: int, spans: list[ProtectedSpan]) -> bool:
    if position <= 0 or position >= len(text):
        return False
    if any(span.start < position < span.end for span in spans):
        return False
    previous = text[position - 1]
    current = text[position]
    if is_latin_char(previous) and is_latin_char(current):
        return False
    if current in CLOSING_PUNCTUATION:
        return False
    return True


def boundary_cost(text: str, position: int, target: int, spans: list[ProtectedSpan]) -> float:
    if not boundary_allowed(text, position, spans):
        return math.inf

    cost = abs(position - target) * 0.35
    previous = text[position - 1]
    current = text[position]
    right = text[position:].lstrip()

    if previous in SENTENCE_PUNCTUATION:
        cost -= 32
    elif previous in BOUNDARY_PUNCTUATION:
        cost -= 18
    if previous.isspace() or current.isspace():
        cost -= 4
    if right and right[0] in CLOSING_PUNCTUATION:
        cost += 60
    return cost


def segment_cost(segment: str) -> float:
    if not segment:
        return math.inf
    cost = 0.0
    if is_orphan_punctuation(segment):
        cost += 120
    if LATIN_TOKEN_RE.fullmatch(segment):
        cost += 50
    if has_unbalanced_pairs(segment):
        cost += 18
    return cost


def protected_spans(text: str) -> list[ProtectedSpan]:
    spans: list[ProtectedSpan] = []
    for match in LATIN_CONNECTOR_SEQUENCE_RE.finditer(text):
        spans.append(ProtectedSpan(match.start(), match.end(), "latin_connector_sequence"))
    for match in LATIN_TOKEN_RE.finditer(text):
        spans.append(ProtectedSpan(match.start(), match.end(), "latin_token"))
    for match in LATIN_TOKEN_SEQUENCE_RE.finditer(text):
        spans.append(ProtectedSpan(match.start(), match.end(), "latin_token_sequence"))
    spans.extend(protected_pair_spans(text))
    return merge_spans(spans)


def protected_pair_spans(text: str) -> list[ProtectedSpan]:
    spans: list[ProtectedSpan] = []
    for opener, closer in OPEN_TO_CLOSE.items():
        start = 0
        while start < len(text):
            open_index = text.find(opener, start)
            if open_index < 0:
                break
            close_index = text.find(closer, open_index + 1)
            if close_index < 0:
                break
            end = close_index + len(closer)
            if 2 <= end - open_index <= 80:
                spans.append(ProtectedSpan(open_index, end, "balanced_pair"))
            start = end
    return spans


def merge_spans(spans: list[ProtectedSpan]) -> list[ProtectedSpan]:
    if not spans:
        return []
    ordered = sorted(spans, key=lambda span: (span.start, span.end))
    merged = [ordered[0]]
    for span in ordered[1:]:
        previous = merged[-1]
        if span.start <= previous.end:
            merged[-1] = ProtectedSpan(
                previous.start,
                max(previous.end, span.end),
                previous.reason if previous.reason == span.reason else "protected",
            )
            continue
        merged.append(span)
    return merged


def diagnose_layout_pair(
    left: SubtitleEntry | None,
    right: SubtitleEntry,
    *,
    target_language: str,
) -> LayoutIssue | None:
    del target_language
    right_text = right.translated_text.strip()
    if not right_text:
        return LayoutIssue("empty_translation", "quality", left.index if left else None, right.index, "empty translated cue")
    if is_orphan_punctuation(right_text):
        return LayoutIssue(
            "punctuation_only",
            "auto_merge",
            left.index if left else None,
            right.index,
            "cue contains only punctuation or closing marks",
        )
    if left is None:
        return None

    left_text = left.translated_text.strip()
    if not left_text:
        return None
    if latin_token_split(left_text, right_text):
        return LayoutIssue("latin_token_split", "auto_merge", left.index, right.index, "latin token split across cues")
    if protected_span_split(left_text, right_text):
        return LayoutIssue("protected_span_split", "review", left.index, right.index, "latin phrase may be split across cues")
    if unbalanced_pair_across_boundary(left_text, right_text):
        return LayoutIssue("unbalanced_pair", "auto_merge", left.index, right.index, "paired punctuation split across cues")
    return None


def latin_token_split(left: str, right: str) -> bool:
    left_match = re.search(r"([A-Za-z][A-Za-z0-9_./:+#-]*)$", left)
    right_match = re.match(r"([A-Za-z][A-Za-z0-9_./:+#-]*)", right)
    if not left_match or not right_match:
        return False
    left_token = left_match.group(1)
    right_token = right_match.group(1)
    return bool(right_token and right_token[0].islower() and any(char.islower() for char in left_token))


def protected_span_split(left: str, right: str) -> bool:
    left_match = re.search(r"([A-Za-z0-9][A-Za-z0-9_./:+#-]*)$", left)
    right_match = re.match(r"([A-Za-z0-9][A-Za-z0-9_./:+#-]*)", right)
    if not left_match or not right_match:
        return False
    return bool(left_match.group(1) and right_match.group(1))


def unbalanced_pair_across_boundary(left: str, right: str) -> bool:
    return has_unbalanced_pairs(left) and starts_with_closing_pair(right)


def has_unbalanced_pairs(text: str) -> bool:
    for opener, closer in OPEN_TO_CLOSE.items():
        if opener in {"\"", "'"}:
            continue
        if text.count(opener) > text.count(closer):
            return True
    return False


def starts_with_closing_pair(text: str) -> bool:
    stripped = text.lstrip()
    return bool(stripped and stripped[0] in set(OPEN_TO_CLOSE.values()))


def is_orphan_punctuation(value: str) -> bool:
    stripped = value.strip()
    return bool(stripped) and all(char in ORPHAN_PUNCTUATION for char in stripped)


def split_word_text(text: str, weights: list[float]) -> list[str]:
    words = text.split()
    if len(words) < len(weights):
        return split_text_by_layout_contract(text, weights, protected_spans(text))

    boundaries = weighted_boundaries(len(words), weights)
    pieces: list[str] = []
    start = 0
    for boundary in boundaries:
        split_at = max(start + 1, min(boundary, len(words) - 1))
        pieces.append(" ".join(words[start:split_at]).strip())
        start = split_at
    pieces.append(" ".join(words[start:]).strip())
    return rebalance_empty_pieces(pieces)


def semantic_weights(entries: list[SubtitleEntry]) -> list[float]:
    source_lengths = [max(1, len(normalize_spaces(entry.original_text))) for entry in entries]
    durations = [duration_ms(entry) for entry in entries]
    if not any(duration > 0 for duration in durations):
        return [float(length) for length in source_lengths]

    total_duration = sum(max(0, duration) for duration in durations)
    total_source = sum(source_lengths)
    if total_duration <= 0:
        return [float(length) for length in source_lengths]
    return [
        0.75 * source_length + 0.25 * (max(0, duration) / total_duration * total_source)
        for source_length, duration in zip(source_lengths, durations, strict=True)
    ]


def duration_ms(entry: SubtitleEntry) -> int:
    start = parse_srt_time(entry.start_time)
    end = parse_srt_time(entry.end_time)
    if start is None or end is None:
        return 0
    return max(0, end - start)


def parse_srt_time(value: str) -> int | None:
    match = TIME_RE.match(value.strip())
    if not match:
        return None
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def weighted_boundaries(total_length: int, weights: list[float]) -> list[int]:
    total_weight = sum(weights)
    consumed = 0.0
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


def normalize_spaces(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text.strip())


def strip_trailing_punctuation(text: str) -> str:
    return text.strip().rstrip("，。！？；：、,.!?;:“”‘’\"'）】》」』)]}>…—- ")


def strip_leading_punctuation(text: str) -> str:
    return text.strip().lstrip("，。！？；：、,.!?;:“”‘’\"'）】》」』)]}>…—- ")


def is_latin_char(value: str) -> bool:
    return value.isascii() and value.isalnum()
