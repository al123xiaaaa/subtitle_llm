from __future__ import annotations

from dataclasses import dataclass, field

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.pipeline.quality import ChunkDiagnosis, TranslationIssue
from subtitle_llm.pipeline.semantic_units import SemanticUnit


@dataclass
class RepairBrief:
    intent: str
    output_entries: list[SubtitleEntry]
    semantic_units: list[SemanticUnit]
    current_entries: list[SubtitleEntry]
    diagnosis: ChunkDiagnosis
    selected_indices: set[int] = field(default_factory=set)
    stable_anchors: list[SubtitleEntry] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        lines = [
            f"Repair intent: {self.intent}",
            f"Output timed cues: {len(self.output_entries)}",
            "",
            "Timed cues to repair and output:",
            *self._format_output_entries(),
            "",
            "Suspicious timed cues:",
            *self._format_suspicious_entries(),
            "",
            "Semantic source context:",
            *self._format_semantic_units(),
            "",
            "Readonly stable anchors:",
            *self._format_stable_anchors(),
            "",
            "Quality diagnosis:",
            self.diagnosis.to_prompt_report(),
        ]
        return "\n".join(lines)

    def _format_output_entries(self) -> list[str]:
        if not self.output_entries:
            return ["(none)"]
        lines: list[str] = []
        for local_index, entry in enumerate(self.output_entries, start=1):
            selected = "yes" if entry.index in self.selected_indices else "no"
            lines.extend([
                f"[{local_index}] global {entry.index} | {entry.start_time} --> {entry.end_time} | selected={selected}",
                f"Source: {entry.original_text}",
                f"Current translation: {entry.translated_text or '(empty)'}",
            ])
        return lines

    def _format_suspicious_entries(self) -> list[str]:
        output_indices = {entry.index for entry in self.output_entries}
        current_by_local = {local_index: entry for local_index, entry in enumerate(self.current_entries, start=1)}
        lines: list[str] = []
        for issue in self.diagnosis.issues:
            entry = current_by_local.get(issue.index)
            if entry is None or entry.index not in output_indices:
                continue
            lines.append(self._format_issue(issue, entry))
        if lines:
            return lines
        return ["No deterministic issue is attached directly to the output range; use the selected range intent."]

    def _format_issue(self, issue: TranslationIssue, entry: SubtitleEntry) -> str:
        related = f" related={issue.related_indices}" if issue.related_indices else ""
        return (
            f"- global {entry.index} | local {issue.index} | {issue.issue_type} | {issue.severity}:{related} "
            f"{issue.description} Current translation: {entry.translated_text or '(empty)'}"
        )

    def _format_semantic_units(self) -> list[str]:
        if not self.semantic_units:
            return ["(none)"]
        lines: list[str] = []
        for unit in self.semantic_units:
            lines.extend([
                f"- semantic unit {unit.index}, timed cues {unit.cue_indices}",
                f"  Source: {unit.source_text}",
            ])
        return lines

    def _format_stable_anchors(self) -> list[str]:
        if not self.stable_anchors:
            return ["(none)"]
        lines: list[str] = []
        for entry in self.stable_anchors:
            lines.extend([
                f"- global {entry.index} | {entry.start_time} --> {entry.end_time}",
                f"  Source: {entry.original_text}",
                f"  Confirmed translation: {entry.translated_text or '(empty)'}",
            ])
        return lines
