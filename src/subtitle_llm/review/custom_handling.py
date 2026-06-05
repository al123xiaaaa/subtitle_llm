from __future__ import annotations

import json
import logging

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import DataTable, Footer, Header, LoadingIndicator, ProgressBar, Static

from subtitle_llm.domain import SubtitleEntry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CustomHandlingApp(App):
    CSS_PATH = "custom_handling.css"
    BINDINGS = [
        ("enter", "confirm", "确认继续"),
        ("space", "cascade_retranslate", "从当前行开始重译"),
        ("d", "mark_alignment_drift", "标记对齐漂移"),
        ("a", "toggle_current_retranslation", "标记/取消当前行"),
        ("m", "merge_with_next", "合并当前行与下一行"),
        ("y", "accept_all", "接受全部"),
        ("escape", "clear_marks", "清空标记"),
    ]

    def __init__(
        self,
        subtitle_entries: list[SubtitleEntry],
        temp_file_path: str,
        chunk_index: int = 0,
        total_chunks: int = 1,
        completed_chunks: int = 0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.subtitle_entries = subtitle_entries
        self.temp_file_path = temp_file_path
        self.chunk_index = chunk_index
        self.total_chunks = max(total_chunks, 1)
        self.completed_chunks = min(max(completed_chunks, 0), self.total_chunks)
        self.merge_map: list[dict] = []
        self.alignment_drift_start_row: int | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            LoadingIndicator(id="activity"),
            Static("准备就绪。", id="status"),
            id="status_bar",
        )
        yield Horizontal(
            Static(id="progress_label"),
            ProgressBar(total=self.total_chunks, show_eta=False, id="chunk_progress"),
            id="progress_bar",
        )
        yield Container(DataTable(id="subtitles_table"), id="table_panel")
        yield Container(Static("", id="current_detail"), id="detail_panel")
        yield Footer()

    def on_mount(self) -> None:
        self.apply_default_cascade_from_first_issue()
        table = self.query_one("#subtitles_table", DataTable)
        table.cursor_type = "row"
        self.rebuild_table()
        self.update_progress()
        self.update_detail()
        self.update_status("准备就绪，按 Enter 确认当前标记。")
        logger.info(
            "TUI审核界面已打开: chunk=%s/%s completed=%s entries=%s pending=%s",
            self.chunk_index + 1,
            self.total_chunks,
            self.completed_chunks,
            len(self.subtitle_entries),
            self.pending_count(),
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.update_detail(event.cursor_row)

    async def on_key(self, event: events.Key) -> None:
        handlers = {
            "enter": self.action_confirm,
            "space": self.action_cascade_retranslate,
            "d": self.action_mark_alignment_drift,
            "a": self.action_toggle_current_retranslation,
            "m": self.action_merge_with_next,
            "y": self.action_accept_all,
            "escape": self.action_clear_marks,
        }
        handler = handlers.get(event.key)
        if handler is None:
            return
        event.prevent_default()
        event.stop()
        result = handler()
        if result is not None:
            await result

    async def action_confirm(self) -> None:
        selected_entries = self.selected_entries()
        self.write_data_to_temp_file(
            {
                "selected_subtitle_entries": [entry.to_dict() for entry in selected_entries],
                "tui_completed": True,
                "merge_map": self.merge_map.copy(),
                "alignment_drift_start_index": self.alignment_drift_start_index(),
            }
        )
        logger.info(
            "TUI用户操作: 确认继续 selected_for_retranslation=%s drift_start=%s",
            len(selected_entries),
            self.alignment_drift_start_index(),
        )
        await self.action_quit()

    def action_cascade_retranslate(self) -> None:
        row_index = self.current_row()
        if row_index is None:
            return
        self.alignment_drift_start_row = None
        for index, entry in enumerate(self.subtitle_entries):
            entry.needs_retranslation = index >= row_index
        self.rebuild_table(keep_row=row_index)
        self.update_status(f"已从字幕 {self.subtitle_entries[row_index].index} 开始标记重译。")

    def action_mark_alignment_drift(self) -> None:
        row_index = self.current_row()
        if row_index is None:
            return
        self.alignment_drift_start_row = row_index
        for index in range(row_index, len(self.subtitle_entries)):
            self.subtitle_entries[index].needs_retranslation = True
        self.rebuild_table(keep_row=row_index)
        self.update_status(f"已从字幕 {self.subtitle_entries[row_index].index} 标记对齐漂移。")

    def action_toggle_current_retranslation(self) -> None:
        row_index = self.current_row()
        if row_index is None:
            return
        if self.is_drift_row(row_index):
            self.update_status("当前行属于对齐漂移范围，按 d 移动起点或 Esc 清空标记。")
            return
        entry = self.subtitle_entries[row_index]
        entry.needs_retranslation = not entry.needs_retranslation
        state = "待重译" if entry.needs_retranslation else "已接受"
        self.rebuild_table(keep_row=row_index)
        self.update_status(f"字幕 {entry.index} 已设为{state}。")

    def action_merge_with_next(self) -> None:
        row_index = self.current_row()
        if row_index is None:
            return
        if row_index >= len(self.subtitle_entries) - 1:
            self.update_status("当前行后面没有可合并的字幕。")
            return

        target_entry = self.subtitle_entries[row_index]
        next_entry = self.subtitle_entries[row_index + 1]
        self.merge_map.append(
            {
                "merged_index": target_entry.index,
                "merged_from_indices": [target_entry.index, next_entry.index],
            }
        )

        target_entry.end_time = next_entry.end_time
        target_entry.original_text = join_non_empty_text(target_entry.original_text, next_entry.original_text)
        target_entry.translated_text = join_non_empty_text(target_entry.translated_text, next_entry.translated_text)
        target_entry.needs_retranslation = True
        del self.subtitle_entries[row_index + 1]
        self.adjust_drift_after_merge(row_index)
        self.rebuild_table(keep_row=row_index)
        self.update_status(f"字幕 {target_entry.index} 已与下一行合并，并标记重译。")
        logger.info(
            "TUI用户操作: 合并当前行与下一行 merged_index=%s merged_from=%s",
            target_entry.index,
            [target_entry.index, next_entry.index],
        )

    async def action_accept_all(self) -> None:
        for entry in self.subtitle_entries:
            entry.needs_retranslation = False
        self.alignment_drift_start_row = None
        self.rebuild_table()
        self.update_status("已接受当前片段全部翻译，继续处理后续字幕。")
        self.write_data_to_temp_file(
            {
                "selected_subtitle_entries": [entry.to_dict() for entry in self.subtitle_entries],
                "tui_completed": True,
                "merge_map": self.merge_map.copy(),
                "alignment_drift_start_index": None,
            }
        )
        logger.info("TUI用户操作: 接受全部 entries=%s", len(self.subtitle_entries))
        await self.action_quit()

    def action_clear_marks(self) -> None:
        for entry in self.subtitle_entries:
            entry.needs_retranslation = False
        self.alignment_drift_start_row = None
        self.rebuild_table()
        self.update_status("已清空当前片段的重译标记。")

    def apply_default_cascade_from_first_issue(self) -> None:
        first_issue = next(
            (index for index, entry in enumerate(self.subtitle_entries) if entry.needs_retranslation),
            None,
        )
        if first_issue is None:
            return
        for index, entry in enumerate(self.subtitle_entries):
            entry.needs_retranslation = index >= first_issue

    def rebuild_table(self, keep_row: int | None = None) -> None:
        table = self.query_one("#subtitles_table", DataTable)
        table.clear(columns=True)
        table.add_column("标记", key="mark", width=8)
        table.add_column("字幕序号", key="index", width=10)
        table.add_column("原文预览", key="original_text", width=46)
        table.add_column("译文预览", key="translated_text", width=46)
        table.add_column("处理状态", key="status", width=12)
        for index, entry in enumerate(self.subtitle_entries):
            table.add_row(
                self.mark_text(index),
                str(entry.index),
                self.preview(entry.original_text),
                self.preview(entry.translated_text),
                self.status_text(index),
                key=f"row-{index}",
                height=1,
            )
        if self.subtitle_entries:
            target_row = min(keep_row if keep_row is not None else table.cursor_row, len(self.subtitle_entries) - 1)
            table.move_cursor(row=max(target_row, 0), animate=False)
        self.update_progress()
        self.update_detail()

    def mark_text(self, row_index: int) -> str:
        if self.is_drift_row(row_index):
            return "漂移"
        if self.subtitle_entries[row_index].needs_retranslation:
            return "重译"
        return "-"

    def status_text(self, row_index: int) -> Text:
        if self.is_drift_row(row_index):
            return Text("漂移重译", style="bold magenta")
        if self.subtitle_entries[row_index].needs_retranslation:
            return Text("待重译", style="yellow")
        return Text("已接受", style="green")

    def is_drift_row(self, row_index: int) -> bool:
        return self.alignment_drift_start_row is not None and row_index >= self.alignment_drift_start_row

    def selected_entries(self) -> list[SubtitleEntry]:
        return [
            entry
            for row_index, entry in enumerate(self.subtitle_entries)
            if entry.needs_retranslation or self.is_drift_row(row_index)
        ]

    def alignment_drift_start_index(self) -> int | None:
        if self.alignment_drift_start_row is None:
            return None
        if self.alignment_drift_start_row >= len(self.subtitle_entries):
            return None
        return self.subtitle_entries[self.alignment_drift_start_row].index

    def adjust_drift_after_merge(self, merged_row: int) -> None:
        if self.alignment_drift_start_row is None:
            return
        if self.alignment_drift_start_row in {merged_row, merged_row + 1}:
            self.alignment_drift_start_row = merged_row
        elif self.alignment_drift_start_row > merged_row + 1:
            self.alignment_drift_start_row -= 1
        if self.alignment_drift_start_row is not None:
            for index in range(self.alignment_drift_start_row, len(self.subtitle_entries)):
                self.subtitle_entries[index].needs_retranslation = True

    def pending_count(self) -> int:
        return len(self.selected_entries())

    def current_row(self) -> int | None:
        if not self.subtitle_entries:
            return None
        table = self.query_one("#subtitles_table", DataTable)
        return min(max(table.cursor_row, 0), len(self.subtitle_entries) - 1)

    def update_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def update_progress(self) -> None:
        current_chunk = min(max(self.chunk_index + 1, 1), self.total_chunks)
        active_progress = min(self.completed_chunks + 1, self.total_chunks)
        self.query_one("#progress_label", Static).update(
            f"进度 {active_progress}/{self.total_chunks} | 当前片段 {current_chunk}/{self.total_chunks} | "
            f"待处理 {self.pending_count()}"
        )
        self.query_one("#chunk_progress", ProgressBar).update(
            total=self.total_chunks,
            progress=active_progress,
        )

    def update_detail(self, row_index: int | None = None) -> None:
        if not self.subtitle_entries:
            self.query_one("#current_detail", Static).update("当前片段没有可复核字幕。")
            return
        if row_index is None:
            current_row = self.current_row()
            row = 0 if current_row is None else current_row
        else:
            row = min(max(row_index, 0), len(self.subtitle_entries) - 1)
        previous_entry = self.subtitle_entries[row - 1] if row > 0 else None
        current_entry = self.subtitle_entries[row]
        next_entry = self.subtitle_entries[row + 1] if row + 1 < len(self.subtitle_entries) else None
        detail = "\n".join(
            [
                f"上一条：{self.preview(previous_entry.original_text, 110) if previous_entry else '-'}",
                f"当前原文：{current_entry.original_text}",
                f"当前译文：{current_entry.translated_text or '(空)'}",
                f"下一条：{self.preview(next_entry.original_text, 110) if next_entry else '-'}",
            ]
        )
        self.query_one("#current_detail", Static).update(detail)

    def write_data_to_temp_file(self, data: dict) -> None:
        try:
            with open(self.temp_file_path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=4)
            logger.info("数据已写入临时文件: %s", self.temp_file_path)
        except Exception as exc:
            logger.error("写入数据到临时文件失败: %s", exc)
            self.update_status("写入数据到临时文件失败。")

    def preview(self, value: str, limit: int = 48) -> str:
        text = " ".join((value or "").split())
        if len(text) <= limit:
            return text
        return f"{text[: max(limit - 1, 1)]}…"


def join_non_empty_text(*values: str) -> str:
    return " ".join(value.strip() for value in values if value.strip())
