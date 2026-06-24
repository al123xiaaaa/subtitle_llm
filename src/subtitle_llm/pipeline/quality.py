from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from subtitle_llm.domain import SubtitleEntry


@dataclass
class TranslationIssue:
    index: int
    issue_type: str
    severity: str
    description: str
    original_text: str = ""
    translated_text: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    related_indices: list[int] = field(default_factory=list)

    def prompt_example(self) -> str:
        related = f" Related indices: {format_indices(self.related_indices)}." if self.related_indices else ""
        translated = clip_text(self.translated_text)
        suffix = f' Translation: "{translated}".' if translated else ""
        return f"[{self.index}] {self.issue_type}: {self.description}{related}{suffix}"


@dataclass
class IssueGroup:
    issue_type: str
    severity: str
    affected_indices: list[int]
    description: str
    examples: list[TranslationIssue] = field(default_factory=list)


@dataclass
class ChunkDiagnosis:
    reliability: str
    summary: str
    issue_groups: list[IssueGroup]
    action_hint: str
    total_entries: int
    flagged_entries: int
    issues: list[TranslationIssue] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.issue_groups or self.issues)

    def to_prompt_report(self, max_groups: int = 6, max_examples_per_group: int = 2) -> str:
        if not self.has_issues:
            return (
                f"Chunk reliability: {self.reliability}. No deterministic quality issues were detected. "
                "Use the previous translation only as weak context."
            )

        lines = [
            f"Chunk reliability: {self.reliability}. {self.summary}",
            "Detected issues:",
        ]
        for group in self.issue_groups[:max_groups]:
            lines.append(f"- {group.description}")
            for example in group.examples[:max_examples_per_group]:
                lines.append(f"  - {example.prompt_example()}")
        lines.append(f"Action: {self.action_hint}")
        return "\n".join(lines)


class QualityGate:
    placeholder_markers = (
        "Translation missing line",
        "Translated text for entry",
        "Translated text",
        "翻译缺失",
    )
    commentary_markers = (
        "承接上文",
        "接上文",
        "续上",
        "同上",
        "前文所述",
        "as above",
        "continued from above",
    )
    punctuation = set("，。？！：；“”‘’\"'、,.?!:;()-[]{}<>…—- ")
    consecutive_failure_threshold = 5
    low_reliability_ratio = 0.3
    very_low_reliability_ratio = 0.5

    def mark_entries_for_retranslation(
        self,
        chunk: list[SubtitleEntry],
        translation: str | None = None,
        target_language: str | None = None,
    ) -> bool:
        diagnosis = self.diagnose_chunk(chunk, translation=translation, target_language=target_language)
        self.apply_diagnosis(chunk, diagnosis)
        return diagnosis.has_issues

    def apply_diagnosis(self, chunk: list[SubtitleEntry], diagnosis: ChunkDiagnosis) -> None:
        for entry in chunk:
            entry.needs_retranslation = False
        for issue in diagnosis.issues:
            if 1 <= issue.index <= len(chunk):
                chunk[issue.index - 1].needs_retranslation = True

    def needs_retranslation(self, entry: SubtitleEntry) -> bool:
        return bool(self.diagnose_entry(entry, index=1))

    def diagnose_chunk(
        self,
        chunk: list[SubtitleEntry],
        translation: str | None = None,
        target_language: str | None = None,
    ) -> ChunkDiagnosis:
        issues: list[TranslationIssue] = []
        if translation is not None:
            issues.extend(self.diagnose_output_structure(translation, expected_count=len(chunk)))

        for index, entry in enumerate(chunk, start=1):
            issues.extend(self.diagnose_entry(entry, index=index, target_language=target_language))

        issues.extend(self.diagnose_duplicate_translations(chunk))

        flagged_indices = sorted({issue.index for issue in issues if 1 <= issue.index <= len(chunk)})
        if not flagged_indices and issues and chunk:
            flagged_indices = [max(1, min(issues[0].index, len(chunk)))]
        issue_groups = self.group_issues(issues, total_entries=len(chunk), flagged_indices=flagged_indices)
        reliability = self.score_reliability(issue_groups, len(chunk), len(flagged_indices))
        summary = self.build_summary(reliability, len(chunk), len(flagged_indices), issue_groups)
        action_hint = self.build_action_hint(reliability, issue_groups)
        return ChunkDiagnosis(
            reliability=reliability,
            summary=summary,
            issue_groups=issue_groups,
            action_hint=action_hint,
            total_entries=len(chunk),
            flagged_entries=len(flagged_indices),
            issues=issues,
        )

    def diagnose_output_structure(self, translation: str, expected_count: int) -> list[TranslationIssue]:
        issues: list[TranslationIssue] = []
        indices = [
            int(match.group(1))
            for line in translation.splitlines()
            if (match := re.fullmatch(r"\[(\d+)\]", line.strip()))
        ]
        if not indices and expected_count:
            return [
                TranslationIssue(
                    index=1,
                    issue_type="index_missing",
                    severity="high",
                    description="No parseable [index] markers were found in the previous output.",
                    metrics={"expected_count": expected_count},
                )
            ]

        unique_indices = set(indices)
        missing = [index for index in range(1, expected_count + 1) if index not in unique_indices]
        if missing:
            issues.append(
                TranslationIssue(
                    index=missing[0],
                    issue_type="index_missing",
                    severity="high",
                    description=(
                        f"Missing output indices {format_indices(missing)}. The previous output may have "
                        "stopped early or broken the required format."
                    ),
                    metrics={"missing_indices": missing},
                    related_indices=missing[1:],
                )
            )

        extra = [index for index in indices if index < 1 or index > expected_count]
        if extra:
            issues.append(
                TranslationIssue(
                    index=extra[0],
                    issue_type="index_extra",
                    severity="medium",
                    description=f"Unexpected output indices {format_indices(sorted(set(extra)))} were found.",
                    metrics={"extra_indices": sorted(set(extra)), "expected_count": expected_count},
                    related_indices=sorted(set(extra))[1:],
                )
            )

        in_range_indices = [index for index in indices if 1 <= index <= expected_count]
        if in_range_indices != sorted(in_range_indices):
            issues.append(
                TranslationIssue(
                    index=in_range_indices[0] if in_range_indices else 1,
                    issue_type="index_order_error",
                    severity="medium",
                    description="Output indices are not in ascending order, so entry alignment is less reliable.",
                    metrics={"observed_indices": in_range_indices[:20]},
                )
            )

        return issues

    def diagnose_entry(
        self,
        entry: SubtitleEntry,
        index: int,
        target_language: str | None = None,
    ) -> list[TranslationIssue]:
        original = entry.original_text.strip()
        translated = entry.translated_text.strip()
        issues: list[TranslationIssue] = []

        if not translated:
            issues.append(
                TranslationIssue(
                    index=index,
                    issue_type="missing_translation",
                    severity="high",
                    description="Translation is empty.",
                    original_text=original,
                    translated_text=translated,
                )
            )
            return issues

        if any(marker in translated for marker in self.placeholder_markers):
            issues.append(
                TranslationIssue(
                    index=index,
                    issue_type="placeholder_translation",
                    severity="high",
                    description="Translation contains placeholder text instead of usable translated content.",
                    original_text=original,
                    translated_text=translated,
                )
            )
            return issues

        if any(marker.lower() in translated.lower() for marker in self.commentary_markers):
            issues.append(
                TranslationIssue(
                    index=index,
                    issue_type="commentary_marker",
                    severity="high",
                    description="Translation contains explanatory carry-over text that should not appear in subtitles.",
                    original_text=original,
                    translated_text=translated,
                )
            )

        if self.is_punctuation_only(translated):
            issues.append(
                TranslationIssue(
                    index=index,
                    issue_type="punctuation_only",
                    severity="high",
                    description="Translation contains only punctuation.",
                    original_text=original,
                    translated_text=translated,
                )
            )
            return issues

        original_len = len(original)
        translated_len = len(translated)
        ratio = translated_len / max(original_len, 1)
        if original_len > 26 and translated_len - 2 < 0.1 * original_len:
            issues.append(
                TranslationIssue(
                    index=index,
                    issue_type="too_short",
                    severity="medium",
                    description=(
                        f"Translation is much shorter than the source: source {original_len} chars, "
                        f"translation {translated_len} chars, ratio {ratio:.1%}."
                    ),
                    original_text=original,
                    translated_text=translated,
                    metrics={"source_chars": original_len, "translation_chars": translated_len, "ratio": ratio},
                )
            )
        elif original_len > 100 and translated_len < 0.19 * original_len:
            issues.append(
                TranslationIssue(
                    index=index,
                    issue_type="too_short",
                    severity="medium",
                    description=(
                        f"Translation is shorter than expected for a long source line: source {original_len} chars, "
                        f"translation {translated_len} chars, ratio {ratio:.1%}."
                    ),
                    original_text=original,
                    translated_text=translated,
                    metrics={"source_chars": original_len, "translation_chars": translated_len, "ratio": ratio},
                )
            )

        if self.is_too_long(original_len, translated_len, target_language):
            issues.append(
                TranslationIssue(
                    index=index,
                    issue_type="too_long",
                    severity="medium",
                    description=(
                        f"Translation is unusually long: source {original_len} chars, "
                        f"translation {translated_len} chars, ratio {ratio:.1%}."
                    ),
                    original_text=original,
                    translated_text=translated,
                    metrics={"source_chars": original_len, "translation_chars": translated_len, "ratio": ratio},
                )
            )

        if self.is_source_copied(original, translated):
            issues.append(
                TranslationIssue(
                    index=index,
                    issue_type="source_copied",
                    severity="medium",
                    description="Translation is identical or highly overlapping with the source text.",
                    original_text=original,
                    translated_text=translated,
                )
            )

        if self.has_target_language_mismatch(translated, target_language):
            issues.append(
                TranslationIssue(
                    index=index,
                    issue_type="target_language_mismatch",
                    severity="medium",
                    description=f"Translation does not appear to be primarily in the target language: {target_language}.",
                    original_text=original,
                    translated_text=translated,
                )
            )

        missing_numbers = self.missing_number_tokens(original, translated)
        if missing_numbers:
            issues.append(
                TranslationIssue(
                    index=index,
                    issue_type="number_mismatch",
                    severity="medium",
                    description=f"Source numeric tokens are missing from the translation: {', '.join(missing_numbers)}.",
                    original_text=original,
                    translated_text=translated,
                    metrics={"missing_numbers": missing_numbers},
                )
            )

        missing_literals = self.missing_literal_tokens(original, translated)
        if missing_literals:
            issues.append(
                TranslationIssue(
                    index=index,
                    issue_type="url_or_code_loss",
                    severity="medium",
                    description=f"Source URL, command, path, or code-like token is missing: {', '.join(missing_literals)}.",
                    original_text=original,
                    translated_text=translated,
                    metrics={"missing_literals": missing_literals},
                )
            )

        return issues

    def diagnose_duplicate_translations(self, chunk: list[SubtitleEntry]) -> list[TranslationIssue]:
        grouped: dict[str, list[tuple[int, SubtitleEntry]]] = defaultdict(list)
        for index, entry in enumerate(chunk, start=1):
            key = normalize_text(entry.translated_text)
            if len(key) >= 4 and not any(normalize_text(marker) in key for marker in self.placeholder_markers):
                grouped[key].append((index, entry))

        issues: list[TranslationIssue] = []
        for _translation_key, entries in grouped.items():
            if len(entries) <= 1:
                continue
            original_keys = {normalize_text(entry.original_text) for _, entry in entries}
            translation_text = entries[0][1].translated_text.strip()
            if len(original_keys) <= 1:
                continue
            if len(entries) < 3 and len(normalize_text(translation_text)) < 8:
                continue
            indices = [index for index, _ in entries]
            for index, entry in entries:
                issues.append(
                    TranslationIssue(
                        index=index,
                        issue_type="duplicate_translation",
                        severity="medium",
                        description=(
                            f"Translation is duplicated across {format_indices(indices)} even though the source "
                            "lines differ."
                        ),
                        original_text=entry.original_text.strip(),
                        translated_text=entry.translated_text.strip(),
                        metrics={"duplicate_indices": indices},
                        related_indices=[candidate for candidate in indices if candidate != index],
                    )
                )

        issues.extend(self.diagnose_adjacent_near_duplicates(chunk))
        return issues

    def diagnose_adjacent_near_duplicates(self, chunk: list[SubtitleEntry]) -> list[TranslationIssue]:
        runs: list[list[int]] = []
        current_run: list[int] = []
        for index in range(1, len(chunk)):
            previous = chunk[index - 1]
            current = chunk[index]
            previous_translation = normalize_text(previous.translated_text)
            current_translation = normalize_text(current.translated_text)
            if len(previous_translation) < 8 or len(current_translation) < 8:
                current_run = []
                continue
            translation_similarity = SequenceMatcher(None, previous_translation, current_translation).ratio()
            source_similarity = SequenceMatcher(
                None,
                normalize_text(previous.original_text),
                normalize_text(current.original_text),
            ).ratio()
            if translation_similarity >= 0.92 and source_similarity < 0.65:
                if not current_run:
                    current_run = [index]
                current_run.append(index + 1)
            else:
                if len(current_run) >= 3:
                    runs.append(current_run)
                current_run = []
        if len(current_run) >= 3:
            runs.append(current_run)

        issues: list[TranslationIssue] = []
        for run in runs:
            for index in run:
                entry = chunk[index - 1]
                issues.append(
                    TranslationIssue(
                        index=index,
                        issue_type="duplicate_translation",
                        severity="medium",
                        description=(
                            f"Translation is highly similar across adjacent entries {format_indices(run)} while "
                            "the source lines differ."
                        ),
                        original_text=entry.original_text.strip(),
                        translated_text=entry.translated_text.strip(),
                        metrics={"duplicate_indices": run, "near_duplicate": True},
                        related_indices=[candidate for candidate in run if candidate != index],
                    )
                )
        return issues

    def group_issues(
        self,
        issues: list[TranslationIssue],
        total_entries: int,
        flagged_indices: list[int],
    ) -> list[IssueGroup]:
        groups: list[IssueGroup] = []
        failure_ratio = len(flagged_indices) / max(total_entries, 1)
        if failure_ratio >= self.low_reliability_ratio:
            severity = "high" if failure_ratio >= self.very_low_reliability_ratio else "medium"
            groups.append(
                IssueGroup(
                    issue_type="high_failure_ratio",
                    severity=severity,
                    affected_indices=flagged_indices,
                    description=(
                        f"{len(flagged_indices)} of {total_entries} entries were flagged by deterministic checks "
                        f"({failure_ratio:.0%}), so the previous translation is broadly unreliable."
                    ),
                    examples=self.pick_examples(issues, flagged_indices),
                )
            )

        cascade = self.longest_consecutive_run(flagged_indices)
        if len(cascade) >= self.consecutive_failure_threshold:
            groups.append(
                IssueGroup(
                    issue_type="cascade_failure",
                    severity="high",
                    affected_indices=cascade,
                    description=(
                        f"{format_indices(cascade)} are consecutive flagged entries. The previous output likely "
                        f"failed from [{cascade[0]}] onward or lost alignment across this span."
                    ),
                    examples=self.pick_examples(issues, cascade),
                )
            )

        by_type: dict[str, list[TranslationIssue]] = defaultdict(list)
        for issue in issues:
            by_type[issue.issue_type].append(issue)

        priority = [
            "index_missing",
            "index_extra",
            "index_order_error",
            "missing_translation",
            "placeholder_translation",
            "commentary_marker",
            "punctuation_only",
            "duplicate_translation",
            "too_short",
            "too_long",
            "source_copied",
            "target_language_mismatch",
            "number_mismatch",
            "url_or_code_loss",
        ]
        for issue_type in priority:
            typed = by_type.get(issue_type, [])
            if not typed:
                continue
            affected = sorted(
                {
                    related_index
                    for issue in typed
                    for related_index in [issue.index, *issue.related_indices]
                    if related_index > 0
                }
            )
            groups.append(
                IssueGroup(
                    issue_type=issue_type,
                    severity=self.max_severity(typed),
                    affected_indices=affected,
                    description=self.describe_group(issue_type, typed, affected),
                    examples=typed[:2],
                )
            )

        return self.dedupe_groups(groups)

    def describe_group(self, issue_type: str, issues: list[TranslationIssue], affected: list[int]) -> str:
        count = len(affected) or len(issues)
        indices = format_indices(affected) if affected else "the previous output"
        descriptions = {
            "index_missing": f"{indices} are missing from the parsed output.",
            "index_extra": f"{indices} include unexpected indices outside the chunk range.",
            "index_order_error": "Output indices are out of order, so alignment is unreliable.",
            "missing_translation": f"{indices} are empty translations ({count} entries).",
            "placeholder_translation": f"{indices} contain placeholder text instead of real translations.",
            "commentary_marker": f"{indices} contain explanatory carry-over text that should not appear in subtitles.",
            "punctuation_only": f"{indices} contain only punctuation.",
            "duplicate_translation": f"{indices} reuse the same or highly similar translation while source lines differ.",
            "too_short": f"{indices} are much shorter than their source lines.",
            "too_long": f"{indices} are unusually long compared with their source lines.",
            "source_copied": f"{indices} appear to copy the source text instead of translating it.",
            "target_language_mismatch": f"{indices} do not appear to be primarily in the target language.",
            "number_mismatch": f"{indices} are missing numeric tokens that appear in the source text.",
            "url_or_code_loss": f"{indices} lost source URL, command, path, or code-like tokens.",
        }
        return descriptions.get(issue_type, f"{indices} have {issue_type} issues.")

    def score_reliability(self, groups: list[IssueGroup], total_entries: int, flagged_count: int) -> str:
        failure_ratio = flagged_count / max(total_entries, 1)
        issue_types = {group.issue_type for group in groups}
        if failure_ratio >= self.very_low_reliability_ratio:
            return "very_low"
        if "cascade_failure" in issue_types and flagged_count >= self.consecutive_failure_threshold:
            return "very_low"
        if failure_ratio >= self.low_reliability_ratio:
            return "low"
        if groups:
            return "medium"
        return "high"

    def build_summary(
        self,
        reliability: str,
        total_entries: int,
        flagged_entries: int,
        issue_groups: list[IssueGroup],
    ) -> str:
        if not issue_groups:
            return f"0 of {total_entries} entries were flagged by deterministic checks."
        main_issue = issue_groups[0].description
        return (
            f"{flagged_entries} of {total_entries} entries were flagged by deterministic checks. "
            f"Main issue: {main_issue}"
        )

    def build_action_hint(self, reliability: str, issue_groups: list[IssueGroup]) -> str:
        if reliability == "very_low":
            return (
                "Ignore the previous translation as a translation source. Re-translate the full chunk from the "
                "original text and preserve exact one-entry-in, one-entry-out alignment."
            )
        if reliability == "low":
            return (
                "Treat the flagged spans as unreliable. Use the previous translation only to avoid repeating the "
                "listed failures, and translate the full chunk from the original text."
            )
        if issue_groups:
            return (
                "Pay special attention to the flagged entries, avoid the listed deterministic failures, and still "
                "return a complete full-chunk re-translation."
            )
        return "Translate the full chunk from the original text."

    def dedupe_groups(self, groups: list[IssueGroup]) -> list[IssueGroup]:
        deduped: list[IssueGroup] = []
        seen: set[tuple[str, tuple[int, ...]]] = set()
        for group in groups:
            key = (group.issue_type, tuple(group.affected_indices))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(group)
        return deduped

    def pick_examples(self, issues: list[TranslationIssue], affected_indices: list[int]) -> list[TranslationIssue]:
        affected = set(affected_indices)
        return [issue for issue in issues if issue.index in affected][:2]

    def longest_consecutive_run(self, indices: list[int]) -> list[int]:
        if not indices:
            return []
        best: list[int] = []
        current = [indices[0]]
        for index in indices[1:]:
            if index == current[-1] + 1:
                current.append(index)
            else:
                if len(current) > len(best):
                    best = current
                current = [index]
        return current if len(current) > len(best) else best

    def max_severity(self, issues: list[TranslationIssue]) -> str:
        order = {"low": 0, "medium": 1, "high": 2}
        return max((issue.severity for issue in issues), key=lambda severity: order.get(severity, 0))

    def is_punctuation_only(self, value: str) -> bool:
        stripped = value.strip()
        return bool(stripped) and all(char in self.punctuation for char in stripped)

    def is_too_long(self, original_len: int, translated_len: int, target_language: str | None) -> bool:
        if original_len <= 26:
            return False
        ratio = translated_len / max(original_len, 1)
        if self.is_chinese_target(target_language):
            return translated_len > 80 and ratio > 1.25
        return translated_len > 80 and ratio > 2.2

    def is_source_copied(self, original: str, translated: str) -> bool:
        original_key = normalize_text(original)
        translated_key = normalize_text(translated)
        if len(original_key) < 8 or len(translated_key) < 8:
            return False
        if original_key == translated_key:
            return True
        return len(original_key) >= 20 and original_key in translated_key

    def has_target_language_mismatch(self, translated: str, target_language: str | None) -> bool:
        if not target_language:
            return False
        target = target_language.lower()
        latin_ratio = char_ratio(translated, lambda char: bool(re.match(r"[A-Za-z]", char)))
        chinese_ratio = char_ratio(translated, is_chinese_char)
        if self.is_chinese_target(target) and len(translated) >= 8:
            return latin_ratio > 0.65 and chinese_ratio < 0.15
        if "english" in target or target in {"en", "eng"}:
            return chinese_ratio > 0.4
        return False

    def is_chinese_target(self, target_language: str | None) -> bool:
        if not target_language:
            return False
        target = target_language.lower()
        return target in {"zh", "zh-cn", "zh_cn"} or "chinese" in target or "中文" in target

    def missing_number_tokens(self, original: str, translated: str) -> list[str]:
        original_numbers = extract_number_tokens(original)
        if not original_numbers:
            return []
        translated_numbers = number_token_equivalents(extract_number_tokens(translated))
        return [
            number
            for number in original_numbers
            if number_token_equivalents([number]).isdisjoint(translated_numbers)
        ][:5]

    def missing_literal_tokens(self, original: str, translated: str) -> list[str]:
        literals = extract_literal_tokens(original)
        if not literals:
            return []
        return [literal for literal in literals if literal not in translated][:5]


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def clip_text(value: str, limit: int = 80) -> str:
    value = " ".join(value.strip().split())
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def format_indices(indices: list[int]) -> str:
    if not indices:
        return ""
    sorted_indices = sorted(set(indices))
    ranges: list[str] = []
    start = previous = sorted_indices[0]
    for index in sorted_indices[1:]:
        if index == previous + 1:
            previous = index
            continue
        ranges.append(format_index_range(start, previous))
        start = previous = index
    ranges.append(format_index_range(start, previous))
    return ", ".join(ranges)


def format_index_range(start: int, end: int) -> str:
    return f"[{start}]" if start == end else f"[{start}]-[{end}]"


def char_ratio(value: str, predicate) -> float:
    chars = [char for char in value if not char.isspace()]
    if not chars:
        return 0.0
    return sum(1 for char in chars if predicate(char)) / len(chars)


def is_chinese_char(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def extract_number_tokens(value: str) -> list[str]:
    tokens = re.findall(r"\d+(?:[.,:/-]\d+)*(?:%|[A-Za-z]+|[十百千万亿]+|年代)?", value)
    return [re.sub(r"[^\dA-Za-z%十百千万亿年代]", "", token) for token in tokens]


def number_token_equivalents(tokens: list[str]) -> set[str]:
    equivalents: set[str] = set()
    for token in tokens:
        normalized = token.strip().lower()
        if not normalized:
            continue
        equivalents.add(normalized)

        if normalized.endswith("s") and normalized[:-1].isdigit():
            equivalents.add(f"decade:{normalized[:-1]}")
        if normalized.endswith("年代") and normalized[:-2].isdigit():
            equivalents.add(f"decade:{normalized[:-2]}")

        match = re.fullmatch(r"(\d+)([十百千万亿])", normalized)
        if match:
            value = int(match.group(1)) * chinese_number_unit_multiplier(match.group(2))
            equivalents.add(str(value))
    return equivalents


def chinese_number_unit_multiplier(unit: str) -> int:
    return {
        "十": 10,
        "百": 100,
        "千": 1_000,
        "万": 10_000,
        "亿": 100_000_000,
    }[unit]


def extract_literal_tokens(value: str) -> list[str]:
    literals: list[str] = []
    literals.extend(re.findall(r"https?://\S+|www\.\S+", value))
    literals.extend(re.findall(r"`([^`]+)`", value))
    literals.extend(re.findall(r"\b[\w./-]+\.(?:py|json|yaml|yml|srt|txt|js|ts|tsx|jsx|md|sh)\b", value))
    literals.extend(
        re.findall(
            r"\b(?:npm|pnpm|yarn|pip|python3?|git|docker|kubectl|curl)\s+[^\n,.;，。]+",
            value,
        )
    )
    deduped: list[str] = []
    for literal in literals:
        stripped = literal.strip()
        if stripped and stripped not in deduped:
            deduped.append(stripped)
    return deduped
