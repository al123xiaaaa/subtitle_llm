from __future__ import annotations

import json
import logging

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, LoadingIndicator, ProgressBar, Static

from subtitle_llm.domain import SubtitleEntry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CustomHandlingApp(App):
    CSS_PATH = "custom_handling.css"
    BINDINGS = [
        ("q", "quit", "确认并继续"),
        ("space", "select_line", "选择行并翻译"),
        ("s", "skip", "跳过"),
        ("a", "toggle_select", "选择多行"),
        ("m", "merge_selected", "合并选中行"),
    ]

    selected_lines = reactive(set())
    merge_map = reactive([])

    def __init__(
        self,
        subtitle_entries: list[SubtitleEntry],
        temp_file_path: str,
        chunk_index: int = 0,
        total_chunks: int = 1,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.subtitle_entries = subtitle_entries
        self.temp_file_path = temp_file_path
        self.chunk_index = chunk_index
        self.total_chunks = max(total_chunks, 1)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            LoadingIndicator(id="activity"),
            Static("等待审核操作。", id="status"),
            id="status_bar",
        )
        yield Horizontal(
            Static(id="progress_label"),
            ProgressBar(total=self.total_chunks, show_eta=False, id="chunk_progress"),
            id="progress_bar",
        )
        yield Container(DataTable(id="subtitles_table"), id="table_panel")
        yield Footer()

    def on_mount(self):
        table = self.query_one("#subtitles_table", DataTable)
        table.add_column("Selected", key="selected", width=10)
        table.add_column("Index", key="index", width=10)
        table.add_column("Original Text", key="original_text", width=70)
        table.add_column("Translated Text", key="translated_text", width=70)
        table.add_column("Needs Retranslation", key="needs_retranslation", width=15)
        table.cursor_type = "row"
        for i, entry in enumerate(self.subtitle_entries):
            table.add_row(
                "No",
                f"{i}",
                entry.original_text,
                entry.translated_text,
                "Yes" if entry.needs_retranslation else "No",
                key=f"row-{i}",
                height=2,
            )
        table.scroll_end()
        self.update_progress()
        self.update_status("准备就绪，等待审核操作。")
        logger.info(
            "TUI审核界面已打开: chunk=%s/%s entries=%s flagged=%s",
            self.chunk_index + 1,
            self.total_chunks,
            len(self.subtitle_entries),
            len([entry for entry in self.subtitle_entries if entry.needs_retranslation]),
        )

    async def on_key(self, event: events.Key) -> None:
        table = self.query_one("#subtitles_table", DataTable)
        if event.key == "q":
            await self.on_quit()
        elif event.key == "space":
            row_index = table.cursor_row
            if row_index is not None:
                selected_entries = self.handle_spacebar_selection(row_index)
                self.write_data_to_temp_file(
                    {
                        "selected_subtitle_entries": [entry.to_dict() for entry in selected_entries],
                        "merge_map": self.merge_map.copy(),
                    }
                )
                logger.info(
                    "TUI用户操作: 从行%s开始标记重译 selected=%s",
                    row_index,
                    len(selected_entries),
                )
                self.update_status("选中的行已写入以供翻译。")
        elif event.key == "s":
            await self.on_key_s()
        elif event.key == "a":
            row_index = table.cursor_row
            if row_index is not None:
                self.toggle_selection(f"row-{row_index}")
        elif event.key == "m":
            await self.merge_selected_rows()
        elif event.key == "escape" and self.selected_lines:
            self.clear_selection()
            self.update_status("已取消选择。")

    async def on_quit(self):
        for entry in self.subtitle_entries:
            if entry.needs_retranslation:
                entry.translated_text = ""
        selected_count = len([entry for entry in self.subtitle_entries if entry.needs_retranslation])
        self.write_data_to_temp_file(
            {
                "selected_subtitle_entries": [
                    entry.to_dict() for entry in self.subtitle_entries if entry.needs_retranslation
                ],
                "tui_completed": True,
                "merge_map": self.merge_map.copy(),
            }
        )
        logger.info("TUI用户操作: 确认并继续 selected_for_retranslation=%s", selected_count)
        await self.action_quit()

    async def on_key_s(self):
        for entry in self.subtitle_entries:
            entry.needs_retranslation = False
        self.update_retranslation_status(0, len(self.subtitle_entries), False)
        self.update_status("所有翻译已接受。跳过重新翻译。")
        self.write_data_to_temp_file(
            {
                "selected_subtitle_entries": [entry.to_dict() for entry in self.subtitle_entries],
                "tui_completed": True,
                "merge_map": self.merge_map.copy(),
            }
        )
        logger.info("TUI用户操作: 跳过重译 entries=%s", len(self.subtitle_entries))
        await self.action_quit()

    def handle_spacebar_selection(self, row_index: int) -> list[SubtitleEntry]:
        total_entries = len(self.subtitle_entries)
        self.update_retranslation_status(0, row_index, False)
        self.update_retranslation_status(row_index, total_entries, True)
        return self.subtitle_entries[row_index:total_entries]

    def update_retranslation_status(self, start: int, end: int, needs_retranslation: bool):
        table = self.query_one("#subtitles_table", DataTable)
        status_text = "Yes" if needs_retranslation else "No"
        for i in range(start, end):
            if i < table.row_count:
                row_key = f"row-{i}"
                self.subtitle_entries[i].needs_retranslation = needs_retranslation
                table.update_cell(row_key, "needs_retranslation", status_text)
                if needs_retranslation:
                    table.add_class("highlighted", row_key)
                else:
                    table.remove_class("highlighted", row_key)

    def toggle_selection(self, row_key: str):
        table = self.query_one("#subtitles_table", DataTable)
        if row_key in self.selected_lines:
            self.selected_lines.remove(row_key)
            table.remove_class("selected", row_key)
            table.update_cell(row_key, "selected", "No")
            self.update_status(f"取消选择行: {row_key}")
            logger.info("TUI用户操作: 取消选择行 row=%s", row_key)
            return

        selected_indices = sorted([int(row.split("-")[1]) for row in self.selected_lines])
        current_index = int(row_key.split("-")[1])
        if self.selected_lines:
            min_selected = min(selected_indices)
            max_selected = max(selected_indices)
            if current_index not in {min_selected - 1, max_selected + 1}:
                self.clear_selection()

        self.selected_lines.add(row_key)
        table.add_class("selected", row_key)
        table.update_cell(row_key, "selected", "Yes")
        self.update_status(f"选择行: {row_key}")
        logger.info("TUI用户操作: 选择行 row=%s selected_count=%s", row_key, len(self.selected_lines))

    def clear_selection(self):
        table = self.query_one("#subtitles_table", DataTable)
        for row_key in self.selected_lines:
            table.remove_class("selected", row_key)
            table.update_cell(row_key, "selected", "No")
        self.selected_lines.clear()

    async def merge_selected_rows(self):
        if len(self.selected_lines) < 2:
            self.update_status("需要至少选择两行以合并。")
            return

        selected_indices = sorted([int(row.split("-")[1]) for row in self.selected_lines])
        for i in range(1, len(selected_indices)):
            if selected_indices[i] != selected_indices[i - 1] + 1:
                self.update_status("请选择相邻的行进行合并。")
                return

        target_index = selected_indices[0]
        self.merge_map.append(
            {
                "merged_index": self.subtitle_entries[target_index].index,
                "merged_from_indices": [self.subtitle_entries[i].index for i in selected_indices],
            }
        )

        target_entry = self.subtitle_entries[target_index]
        target_entry.start_time = self.subtitle_entries[selected_indices[0]].start_time
        target_entry.end_time = self.subtitle_entries[selected_indices[-1]].end_time
        target_entry.original_text = " ".join(
            [self.subtitle_entries[i].original_text for i in selected_indices]
        )
        target_entry.translated_text = ""
        target_entry.needs_retranslation = True

        for i in reversed(selected_indices[1:]):
            del self.subtitle_entries[i]

        self.rebuild_table()
        self.selected_lines.clear()
        self.update_retranslation_status(target_index, len(self.subtitle_entries), True)
        self.update_status("选中的行已合并并标记为需要重新翻译。")
        logger.info(
            "TUI用户操作: 合并行 merged_index=%s merged_from=%s",
            target_entry.index,
            selected_indices,
        )

    def rebuild_table(self):
        table = self.query_one("#subtitles_table", DataTable)
        table.clear(columns=True)
        table.add_column("Selected", key="selected", width=10)
        table.add_column("Index", key="index", width=10)
        table.add_column("Original Text", key="original_text", width=70)
        table.add_column("Translated Text", key="translated_text", width=70)
        table.add_column("Needs Retranslation", key="needs_retranslation", width=15)
        for i, entry in enumerate(self.subtitle_entries):
            table.add_row(
                "No",
                f"{i}",
                entry.original_text,
                entry.translated_text,
                "Yes" if entry.needs_retranslation else "No",
                key=f"row-{i}",
                height=2,
            )

    def write_data_to_temp_file(self, data: dict):
        try:
            with open(self.temp_file_path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=4)
            logger.info("数据已写入临时文件: %s", self.temp_file_path)
            self.update_status("数据已成功写入临时文件。")
        except Exception as exc:
            logger.error("写入数据到临时文件失败: %s", exc)
            self.update_status("写入数据到临时文件失败。")

    def update_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def update_progress(self) -> None:
        progress = min(self.chunk_index + 1, self.total_chunks)
        self.query_one("#progress_label", Static).update(f"Chunk {progress}/{self.total_chunks}")
        self.query_one("#chunk_progress", ProgressBar).update(total=self.total_chunks, progress=progress)
