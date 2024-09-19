from textual.app import App, ComposeResult
from textual.widgets import (
    Header,
    Footer,
    DataTable,
    Button,
    Static,
    LoadingIndicator,
)
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual import events
from textual.message import Message

import asyncio
import re
import logging
import json  # 新增

from src.models.subtitle_entry import SubtitleEntry
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LineSelected(Message):
    """Message sent when a line is selected."""

    def __init__(self, start_line_index: int) -> None:
        self.start_line_index = start_line_index
        super().__init__()


class TranslationFinished(Message):
    """Message sent when translation is finished."""

    def __init__(self, translations: dict) -> None:
        self.translations = translations
        super().__init__()


class CustomHandlingApp(App):
    CSS_PATH = "custom_handling.css"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("space", "select_line", "Select Line and Translate"),
    ]

    # Define reactive variables at the class level
    selected_line = reactive(None)
    translation_result = reactive(None)

    def __init__(
        self,
        subtitle_entries: list[SubtitleEntry],
        target_language: str,
        config: dict,
        temp_file_path: str,  # 新增参数
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.subtitle_entries = subtitle_entries
        self.target_language = target_language
        self.config = config
        self.temp_file_path = temp_file_path  # 保存临时文件路径

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Container(
            DataTable(id="subtitles_table"),
            Static(id="status"),
        )

    def on_mount(self):
        table = self.query_one("#subtitles_table", DataTable)
        table.add_column("Index", key="index", width=10)
        table.add_column("Original Text", key="original_text", width=70)
        table.add_column("Translated Text", key="translated_text", width=70)
        table.add_column("Needs Retranslation", key="needs_retranslation", width=10)
        table.cursor_type = "row"
        for i, entry in enumerate(self.subtitle_entries):
            needs_retranslation = "Yes" if entry.needs_retranslation else "No"
            table.add_row(
                f"row-{i}",
                entry.original_text,
                entry.translated_text,
                needs_retranslation,
                key=f"row-{i}",  # 指定 row_key
                height=2,
            )
        table.scroll_end()
        self.query_one("#status", Static).update("Ready.")

    async def on_key(self, event: events.Key) -> None:
        table = self.query_one("#subtitles_table", DataTable)
        if event.key == "q":
            await self.on_quit()
        elif event.key == "space":
            # 获取当前光标所在行
            row_index = table.cursor_row
            if row_index is not None:
                selected_entries = self.handle_spacebar_selection(row_index)
                # 将选中的条目写入临时文件
                self.write_selected_entries_to_temp_file(selected_entries)
                self.query_one("#status", Static).update(
                    "Selected lines written for translation."
                )
        elif event.key == "escape":
            if self.selected_line is not None:
                table.unhighlight_row(self.selected_line - 1)
                self.selected_line = None

    def handle_spacebar_selection(self, row_index: int) -> list:
        """
        处理空格键选择，选中当前行及其后续所有行。
        """
        start_index = row_index
        total_entries = len(self.subtitle_entries)

        # 高亮选中行及后续行
        table = self.query_one("#subtitles_table", DataTable)

        # Set needs_retranslation to False for rows before the selected row
        for i in range(0, start_index):
            row_key = f"row-{i}"
            self.subtitle_entries[i].needs_retranslation = False
            table.update_cell(row_key, "needs_retranslation", "No")
            table.remove_class("highlighted", row_key)

        for i in range(start_index, total_entries):
            # 添加检查，确保行索引在有效范围内
            if i < table.row_count:
                row_key = f"row-{i}"  # 与添加行时的 row_key 一致
                table.add_class("highlighted", row_key)
                self.subtitle_entries[i].needs_retranslation = True
                table.update_cell(row_key, "needs_retranslation", "Yes")
            else:
                logger.info(
                    f"Row {i} is out of range (table.row_count: {table.row_count})"
                )

        self.selected_line = start_index + 1  # 行索引从1开始

        # 根据选中行索引返回相应数据
        selected_entries = self.subtitle_entries[start_index:total_entries]
        return selected_entries

    def write_selected_entries_to_temp_file(self, selected_entries: list):
        """
        将选中的字幕条目写入临时文件。
        """
        data = {
            "selected_subtitle_entries": [
                entry.to_dict() for entry in selected_entries
            ],
            "target_language": self.target_language,
            "config": self.config,
        }
        try:
            with open(self.temp_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logger.info(
                f"Selected entries written to temporary file: {self.temp_file_path}"
            )
            self.query_one("#status", Static).update(
                "Selected lines written for translation."
            )
        except Exception as e:
            logger.error(f"Failed to write selected entries to temp file: {e}")
            self.query_one("#status", Static).update("Failed to write selected lines.")

    async def on_quit(self):  # 将方法改为异步
        # 将所有需要重新翻译的条目的翻译文本清空
        for entry in self.subtitle_entries:
            if entry.needs_retranslation:
                entry.translated_text = ""

        # Update the temporary file with the completed flag
        data = {
            "selected_subtitle_entries": [
                entry.to_dict()
                for entry in self.subtitle_entries
                if entry.needs_retranslation
            ],
            "target_language": self.target_language,
            "config": self.config,
            "tui_completed": True,
        }
        try:
            with open(self.temp_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logger.info("TUI completed. Updated data written to temporary file.")
        except Exception as e:
            logger.error(f"Failed to write updated data to temp file: {e}")

        # Quit the application
        await self.action_quit()  # 使用 Textual 的内置退出方法
