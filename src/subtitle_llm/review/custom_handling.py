from __future__ import annotations

import json
import logging

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Static

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

    def __init__(self, subtitle_entries: list[SubtitleEntry], temp_file_path: str, **kwargs):
        super().__init__(**kwargs)
        self.subtitle_entries = subtitle_entries
        self.temp_file_path = temp_file_path

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Container(DataTable(id="subtitles_table"), Static(id="status"))

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
        self.query_one("#status", Static).update("准备就绪。")

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
                self.query_one("#status", Static).update("选中的行已写入以供翻译。")
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
            self.query_one("#status", Static).update("已取消选择。")

    async def on_quit(self):
        for entry in self.subtitle_entries:
            if entry.needs_retranslation:
                entry.translated_text = ""
        self.write_data_to_temp_file(
            {
                "selected_subtitle_entries": [
                    entry.to_dict() for entry in self.subtitle_entries if entry.needs_retranslation
                ],
                "tui_completed": True,
                "merge_map": self.merge_map.copy(),
            }
        )
        await self.action_quit()

    async def on_key_s(self):
        for entry in self.subtitle_entries:
            entry.needs_retranslation = False
        self.update_retranslation_status(0, len(self.subtitle_entries), False)
        self.query_one("#status", Static).update("所有翻译已接受。跳过重新翻译。")
        self.write_data_to_temp_file(
            {
                "selected_subtitle_entries": [entry.to_dict() for entry in self.subtitle_entries],
                "tui_completed": True,
                "merge_map": self.merge_map.copy(),
            }
        )
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
            self.query_one("#status", Static).update(f"取消选择行: {row_key}")
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
        self.query_one("#status", Static).update(f"选择行: {row_key}")

    def clear_selection(self):
        table = self.query_one("#subtitles_table", DataTable)
        for row_key in self.selected_lines:
            table.remove_class("selected", row_key)
            table.update_cell(row_key, "selected", "No")
        self.selected_lines.clear()

    async def merge_selected_rows(self):
        if len(self.selected_lines) < 2:
            self.query_one("#status", Static).update("需要至少选择两行以合并。")
            return

        selected_indices = sorted([int(row.split("-")[1]) for row in self.selected_lines])
        for i in range(1, len(selected_indices)):
            if selected_indices[i] != selected_indices[i - 1] + 1:
                self.query_one("#status", Static).update("请选择相邻的行进行合并。")
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
        self.query_one("#status", Static).update("选中的行已合并并标记为需要重新翻译。")

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
            self.query_one("#status", Static).update("数据已成功写入临时文件。")
        except Exception as exc:
            logger.error("写入数据到临时文件失败: %s", exc)
            self.query_one("#status", Static).update("写入数据到临时文件失败。")
