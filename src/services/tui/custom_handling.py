from textual.app import App, ComposeResult
from textual.widgets import (
    Header,
    Footer,
    DataTable,
    Static,
)
from textual.containers import Container
from textual.reactive import reactive
from textual import events

import logging
import json

from src.models.subtitle_entry import SubtitleEntry

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CustomHandlingApp(App):
    CSS_PATH = "custom_handling.css"
    BINDINGS = [
        ("q", "quit", "退出"),
        ("space", "select_line", "选择行并翻译"),
        ("s", "skip", "跳过"),
        ("a", "toggle_select", "选择多行"),
        ("m", "merge_selected", "合并选中行"),
    ]

    # Reactive variable to store selected lines
    selected_lines = reactive(set())  # 使用集合存储选中的行键

    # 新增 merge_map 属性，用于记录合并操作
    merge_map = reactive([])  # 使用列表存储合并映射

    # 1. Lifecycle methods
    def __init__(
        self,
        subtitle_entries: list[SubtitleEntry],
        temp_file_path: str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.subtitle_entries = subtitle_entries
        self.temp_file_path = temp_file_path

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Container(
            DataTable(id="subtitles_table"),
            Static(id="status"),
        )

    def on_mount(self):
        table = self.query_one("#subtitles_table", DataTable)
        table.add_column("Selected", key="selected", width=10)
        table.add_column("Index", key="index", width=10)
        table.add_column("Original Text", key="original_text", width=70)
        table.add_column("Translated Text", key="translated_text", width=70)
        table.add_column("Needs Retranslation", key="needs_retranslation", width=15)
        table.cursor_type = "row"
        for i, entry in enumerate(self.subtitle_entries):
            needs_retranslation = "Yes" if entry.needs_retranslation else "No"
            # Initial 'Selected' column value is 'No'
            table.add_row(
                "No",  # Selected column
                f"{i}",  # Index column
                entry.original_text,
                entry.translated_text,
                needs_retranslation,
                key=f"row-{i}",  # Specify row_key
                height=2,
            )
        table.scroll_end()
        self.query_one("#status", Static).update("准备就绪。")

    # 2. Event handling methods
    async def on_key(self, event: events.Key) -> None:
        table = self.query_one("#subtitles_table", DataTable)
        if event.key == "q":
            await self.on_quit()
        elif event.key == "space":
            # Get the row under the cursor
            row_index = table.cursor_row
            if row_index is not None:
                selected_entries = self.handle_spacebar_selection(row_index)
                # Write the selected entries to the temp file
                self.write_data_to_temp_file(
                    {
                        "selected_subtitle_entries": [
                            entry.to_dict() for entry in selected_entries
                        ],
                        "merge_map": self.merge_map.copy(),  # 将 merge_map 一并写入
                    }
                )
                self.query_one("#status", Static).update("选中的行已写入以供翻译。")
        elif event.key == "s":
            await self.on_key_s()
        elif event.key == "a":
            # Toggle selection of the current row
            row_index = table.cursor_row
            if row_index is not None:
                row_key = f"row-{row_index}"
                self.toggle_selection(row_key)
        elif event.key == "m":
            # Merge selected rows
            await self.merge_selected_rows()
        elif event.key == "escape":
            if self.selected_lines:
                self.clear_selection()
                self.query_one("#status", Static).update("已取消选择。")

    async def on_quit(self):
        """Handle quitting the application"""
        # Clear the translated_text for entries needing retranslation
        for entry in self.subtitle_entries:
            if entry.needs_retranslation:
                entry.translated_text = ""

        # 更新 merge_map 到 temp 文件
        self.write_data_to_temp_file(
            {
                "selected_subtitle_entries": [
                    entry.to_dict()
                    for entry in self.subtitle_entries
                    if entry.needs_retranslation
                ],
                "tui_completed": True,
                "merge_map": self.merge_map.copy(),  # 将 merge_map 一并写入
            }
        )

        # Quit the application
        await self.action_quit()

    async def on_key_s(self):
        """Handle the 's' key press event"""
        for entry in self.subtitle_entries:
            entry.needs_retranslation = False
        # Set all entries to not need retranslation
        self.update_retranslation_status(0, len(self.subtitle_entries), False)

        self.query_one("#status", Static).update("所有翻译已接受。跳过重新翻译。")

        # Update the temp file with a completion flag
        self.write_data_to_temp_file(
            {
                "selected_subtitle_entries": [
                    entry.to_dict() for entry in self.subtitle_entries
                ],
                "tui_completed": True,
                "merge_map": self.merge_map.copy(),  # 将 merge_map 一并写入
            }
        )

        # Quit the application
        await self.action_quit()

    # 3. Core functionality methods
    def handle_spacebar_selection(self, row_index: int) -> list:
        """Handle selection when spacebar is pressed."""
        start_index = row_index
        total_entries = len(self.subtitle_entries)

        # Update retranslation status of entries
        self.update_retranslation_status(0, start_index, False)
        self.update_retranslation_status(start_index, total_entries, True)

        self.selected_line = start_index + 1  # Line index starts from 1
        return self.subtitle_entries[start_index:total_entries]

    def update_retranslation_status(
        self, start: int, end: int, needs_retranslation: bool
    ):
        """Update the retranslation status for a range of rows"""
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
            else:
                logger.info(f"行 {i} 超出范围 (table.row_count: {table.row_count})")

    # 切换选择
    def toggle_selection(self, row_key: str):
        table = self.query_one("#subtitles_table", DataTable)
        if row_key in self.selected_lines:
            self.selected_lines.remove(row_key)
            table.remove_class("selected", row_key)
            # Update 'Selected' column to 'No'
            table.update_cell(row_key, "selected", "No")
            self.query_one("#status", Static).update(f"取消选择行: {row_key}")
        else:
            # Check if the selection is adjacent to existing selection
            selected_indices = sorted(
                [int(rk.split("-")[1]) for rk in self.selected_lines]
            )
            current_index = int(row_key.split("-")[1])
            if not self.selected_lines:
                # No existing selection, select the current row
                self.selected_lines.add(row_key)
                table.add_class("selected", row_key)
                # Update 'Selected' column to 'Yes'
                table.update_cell(row_key, "selected", "Yes")
                self.query_one("#status", Static).update(f"选择行: {row_key}")
            else:
                min_selected = min(selected_indices)
                max_selected = max(selected_indices)
                if (
                    current_index == min_selected - 1
                    or current_index == max_selected + 1
                ):
                    # The row is adjacent to existing selection
                    self.selected_lines.add(row_key)
                    table.add_class("selected", row_key)
                    # Update 'Selected' column to 'Yes'
                    table.update_cell(row_key, "selected", "Yes")
                    self.query_one("#status", Static).update(f"选择行: {row_key}")
                else:
                    # Non-adjacent selection, reset and select current row
                    self.clear_selection()
                    self.selected_lines.add(row_key)
                    table.add_class("selected", row_key)
                    # Update 'Selected' column to 'Yes'
                    table.update_cell(row_key, "selected", "Yes")
                    self.query_one("#status", Static).update(f"选择行: {row_key}")

    def clear_selection(self):
        table = self.query_one("#subtitles_table", DataTable)
        for row_key in self.selected_lines:
            table.remove_class("selected", row_key)
            # Update 'Selected' column to 'No'
            table.update_cell(row_key, "selected", "No")
        self.selected_lines.clear()

    async def merge_selected_rows(self):
        table = self.query_one("#subtitles_table", DataTable)
        if len(self.selected_lines) < 2:
            self.query_one("#status", Static).update("需要至少选择两行以合并。")
            return

        # 获取选中的行的索引
        selected_indices = sorted([int(rk.split("-")[1]) for rk in self.selected_lines])

        # 检查是否相邻
        for i in range(1, len(selected_indices)):
            if selected_indices[i] != selected_indices[i - 1] + 1:
                self.query_one("#status", Static).update("请选择相邻的行进行合并。")
                return

        # 记录被合并的原始索引
        merged_from_indices = selected_indices.copy()

        # 合并条目
        target_index = selected_indices[0]
        target_row_key = f"row-{target_index}"
        merged_original_text = " ".join(
            [self.subtitle_entries[i].original_text for i in selected_indices]
        )
        merged_start_time = self.subtitle_entries[selected_indices[0]].start_time
        merged_end_time = self.subtitle_entries[selected_indices[-1]].end_time

        # 记录合并操作到 merge_map
        self.merge_map.append(
            {
                "merged_index": self.subtitle_entries[target_index].index,
                "merged_from_indices": [
                    self.subtitle_entries[i].index for i in merged_from_indices
                ],
            }
        )

        # 更新目标条目
        target_entry = self.subtitle_entries[target_index]
        target_entry.start_time = merged_start_time
        target_entry.end_time = merged_end_time
        target_entry.original_text = merged_original_text
        target_entry.translated_text = ""
        target_entry.needs_retranslation = True

        # 更新表格中的目标条目
        table.update_cell(target_row_key, "original_text", merged_original_text)
        table.update_cell(target_row_key, "translated_text", "")
        table.update_cell(target_row_key, "needs_retranslation", "Yes")

        # 移除其他被合并的行
        for i in reversed(selected_indices[1:]):
            row_key = f"row-{i}"
            table.remove_row(row_key)
            del self.subtitle_entries[i]
            self.query_one("#status", Static).update(f"已合并并移除行: {row_key}")

        # 重新分配行键和索引
        table.clear(columns=True)
        table.add_column("Selected", key="selected", width=10)
        table.add_column("Index", key="index", width=10)
        table.add_column("Original Text", key="original_text", width=70)
        table.add_column("Translated Text", key="translated_text", width=70)
        table.add_column("Needs Retranslation", key="needs_retranslation", width=15)

        for i, entry in enumerate(self.subtitle_entries):
            needs_retranslation = "Yes" if entry.needs_retranslation else "No"
            table.add_row(
                "No",
                f"{i}",
                entry.original_text,
                entry.translated_text,
                needs_retranslation,
                key=f"row-{i}",
                height=2,
            )

        self.clear_selection()
        self.update_retranslation_status(target_index, len(self.subtitle_entries), True)
        self.query_one("#status", Static).update("选中的行已合并并标记为需要重新翻译。")

    # 4. Helper methods
    def write_data_to_temp_file(self, data: dict):
        """Write data to the temp file"""
        try:
            with open(self.temp_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logger.info(f"数据已写入临时文件: {self.temp_file_path}")
            self.query_one("#status", Static).update("数据已成功写入临时文件。")
        except Exception as e:
            logger.error(f"写入数据到临时文件失败: {e}")
            self.query_one("#status", Static).update("写入数据到临时文件失败。")
