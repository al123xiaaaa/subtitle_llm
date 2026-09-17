from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.pipeline.report import AutoLayoutRepair, TranslationReport
from subtitle_llm.pipeline.task_store import TranslationTaskStore


@dataclass
class TaskStateRestore:
    resumed_indices: set[int]
    ledger: "RunLedger"


@dataclass
class RunLedger:
    removed_entry_indices: set[int] = field(default_factory=set)

    @classmethod
    def restore_task_state(
        cls,
        *,
        resume: bool,
        task_id: str,
        subtitle: Subtitle,
        task_store: TranslationTaskStore,
        report: TranslationReport,
    ) -> TaskStateRestore:
        ledger = cls()
        if not resume:
            ledger.sync_report(report)
            return TaskStateRestore(resumed_indices=set(), ledger=ledger)

        data = task_store.load_resume_state(task_id)
        entries = data.get("entries", {})
        report_data = data.get("report", {})
        ledger.record_removed_indices(
            int(index)
            for index in report_data.get("removed_entry_indices", [])
        )
        failed_entry_indices = {
            int(index)
            for failed in report_data.get("failed_chunks", [])
            for index in failed.get("entry_indices", [])
        }

        resumed_indices: set[int] = set()
        restored_entries: list[SubtitleEntry] = []
        for entry in subtitle.entries:
            if ledger.is_removed(entry.index):
                continue
            if entry.index in failed_entry_indices:
                restored_entries.append(entry)
                continue
            saved = entries.get(str(entry.index))
            # 新记录明确区分已接受与保存时正在复核的源条目；后者必须重新处理。
            if "accepted_entry_indices" in report_data and entry.index not in report_data["accepted_entry_indices"]:
                saved = None
            if not saved:
                restored_entries.append(entry)
                continue
            restored_entry = SubtitleEntry.from_dict(saved)
            restored_entries.append(restored_entry)
            # 暂存译文（needs_retranslation）不算恢复完成，重跑时必须重新翻译。
            if restored_entry.translated_text.strip() and not restored_entry.needs_retranslation:
                resumed_indices.add(restored_entry.index)

        subtitle.entries = restored_entries
        report.resumed_entries = len(resumed_indices)
        report.accepted_entry_indices = sorted(
            resumed_indices & {int(index) for index in report_data.get("accepted_entry_indices", [])}
        )
        report.auto_layout_repairs = [
            AutoLayoutRepair(**item)
            for item in report_data.get("auto_layout_repairs", [])
        ]
        report.final_output_entries = int(report_data.get("final_output_entries", 0) or 0)
        ledger.sync_report(report)
        return TaskStateRestore(resumed_indices=resumed_indices, ledger=ledger)

    def record_removed_indices(self, indices: Iterable[int]) -> None:
        self.removed_entry_indices.update(int(index) for index in indices)

    def record_auto_layout_repair(
        self,
        report: TranslationReport,
        *,
        merged_index: int,
        removed_index: int,
        reason: str,
        source: str = "semantic_layout",
    ) -> AutoLayoutRepair:
        self.record_removed_indices([removed_index])
        repair = AutoLayoutRepair(
            merged_index=merged_index,
            removed_index=removed_index,
            reason=reason,
            source=source,
        )
        report.auto_layout_repairs.append(repair)
        self.sync_report(report)
        return repair

    def is_removed(self, entry_index: int) -> bool:
        return entry_index in self.removed_entry_indices

    def processed_entry_count(self, translated_entries: Iterable[SubtitleEntry]) -> int:
        # 暂存译文（needs_retranslation）尚未通过验收，不计入已处理进度。
        return len({
            entry.index
            for entry in translated_entries
            if not self.is_removed(entry.index) and not entry.needs_retranslation
        })

    def save_task_state(
        self,
        task_store: TranslationTaskStore,
        task_id: str,
        subtitle: Subtitle,
        report: TranslationReport,
        translated_entries: list[SubtitleEntry],
    ) -> None:
        self.sync_report(report)
        report.accepted_entry_indices = sorted(
            set(report.accepted_entry_indices) | {entry.index for entry in translated_entries if not self.is_removed(entry.index)}
        )
        task_store.save_resume_state(task_id, self.resume_state_subtitle(subtitle, translated_entries), report)

    def resume_state_subtitle(
        self,
        subtitle: Subtitle,
        translated_entries: list[SubtitleEntry],
    ) -> Subtitle:
        translated_by_index = {
            entry.index: entry
            for entry in translated_entries
            if not self.is_removed(entry.index)
        }
        checkpoint_entries: list[SubtitleEntry] = []
        seen_indices: set[int] = set()
        for entry in subtitle.entries:
            if self.is_removed(entry.index):
                continue
            checkpoint_entry = translated_by_index.get(entry.index, entry)
            checkpoint_entries.append(checkpoint_entry)
            seen_indices.add(checkpoint_entry.index)

        for entry in translated_entries:
            if self.is_removed(entry.index) or entry.index in seen_indices:
                continue
            checkpoint_entries.append(entry)

        return Subtitle(sorted(checkpoint_entries, key=lambda item: item.index))

    def finalize_subtitle(
        self,
        subtitle: Subtitle,
        translated_entries: list[SubtitleEntry],
        report: TranslationReport,
        *,
        reorder: bool = True,
    ) -> None:
        translated_by_index = {
            entry.index: entry
            for entry in translated_entries
            if not self.is_removed(entry.index)
        }
        for entry in subtitle.entries:
            if self.is_removed(entry.index):
                continue
            if entry.index in translated_by_index:
                continue
            if not entry.translated_text.strip():
                entry.set_translated_text(entry.original_text.strip())
            translated_by_index[entry.index] = entry

        subtitle.entries = sorted(translated_by_index.values(), key=lambda item: item.index)
        if reorder:
            subtitle.reorder_entries()
        report.stage = "完成"
        report.processed_entries = len(subtitle.entries)
        report.final_output_entries = len(subtitle.entries)
        self.sync_report(report)

    def sync_report(self, report: TranslationReport) -> None:
        report.removed_entry_indices = sorted(self.removed_entry_indices)
