"""产物写入采用独立快照与不覆盖发布，编辑不会改动已有文件。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.io import SubtitleIO


def write_snapshot(entries: list[dict], output: Path, output_format: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(suffix='.srt', dir=output.parent)
    os.close(handle)
    try:
        subtitle = Subtitle([SubtitleEntry.from_dict(entry) for entry in entries])
        SubtitleIO.write_srt(subtitle, temporary, output_format=output_format)
        # 同目录原子创建目标；已存在时失败，不覆盖之前导出的任何版本。
        os.link(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
