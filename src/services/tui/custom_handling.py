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
    ]

    # 响应式变量
    selected_line = reactive(None)

    # 1. 生命周期方法
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
        self.query_one("#status", Static).update("准备就绪。")

    # 2. 事件处理方法
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
                self.write_data_to_temp_file(
                    {
                        "selected_subtitle_entries": [
                            entry.to_dict() for entry in selected_entries
                        ]
                    }
                )
                self.query_one("#status", Static).update("选中的行已写入以供翻译。")
        elif event.key == "s":
            await self.on_key_s()
        elif event.key == "escape":
            if self.selected_line is not None:
                table.unhighlight_row(self.selected_line - 1)
                self.selected_line = None

    async def on_quit(self):
        """退出应用前的处理"""
        # 清空需要重新翻译的条目的翻译文本
        for entry in self.subtitle_entries:
            if entry.needs_retranslation:
                entry.translated_text = ""

        # 更新临时文件，添加完成标志
        self.write_data_to_temp_file(
            {
                "selected_subtitle_entries": [
                    entry.to_dict()
                    for entry in self.subtitle_entries
                    if entry.needs_retranslation
                ],
                "tui_completed": True,
            }
        )

        # 退出应用
        await self.action_quit()

    async def on_key_s(self):
        """处理 's' 键按下事件"""
        # 将所有条目设置为不需要重新翻译
        self.update_retranslation_status(0, len(self.subtitle_entries), False)

        self.query_one("#status", Static).update("所有翻译已接受。跳过重新翻译。")

        # 更新临时文件，添加完成标志
        self.write_data_to_temp_file(
            {
                "selected_subtitle_entries": [
                    entry.to_dict() for entry in self.subtitle_entries
                ],
                "tui_completed": True,
            }
        )

        # 退出应用
        await self.action_quit()

    # 3. 核心功能方法
    def handle_spacebar_selection(self, row_index: int) -> list:
        """处理空格键选择，选中当前行及其后续所有行。"""
        start_index = row_index
        total_entries = len(self.subtitle_entries)

        # 更新行的需要重新翻译状态
        self.update_retranslation_status(0, start_index, False)
        self.update_retranslation_status(start_index, total_entries, True)

        self.selected_line = start_index + 1  # 行索引从1开始
        return self.subtitle_entries[start_index:total_entries]

    def update_retranslation_status(
        self, start: int, end: int, needs_retranslation: bool
    ):
        """更新指定范围内行的重新翻译状态"""
        table = self.query_one("#subtitles_table", DataTable)
        status_text = "是" if needs_retranslation else "否"
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

    # 4. 辅助方法
    def write_data_to_temp_file(self, data: dict):
        """将数据写入临时文件"""
        try:
            with open(self.temp_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logger.info(f"数据已写入临时文件: {self.temp_file_path}")
            self.query_one("#status", Static).update("数据已成功写入临时文件。")
        except Exception as e:
            logger.error(f"写入数据到临时文件失败: {e}")
            self.query_one("#status", Static).update("写入数据到临时文件失败。")
