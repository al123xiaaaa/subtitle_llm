"""字幕时间调整的公共约束；不允许越过相邻人工保护内容。"""

import re


def milliseconds(value: str) -> int:
    match = re.fullmatch(r"(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})", value)
    if not match:
        raise ValueError("时间格式应为 HH:MM:SS,mmm")
    hours, minutes, seconds, fraction = map(int, match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + fraction


def validate_timing(before: list[dict], after: list[dict], changed: set[int]) -> None:
    old = {entry["index"]: entry for entry in before}
    for position, entry in enumerate(after):
        if entry["index"] not in changed:
            continue
        start, end = milliseconds(entry["start_time"]), milliseconds(entry["end_time"])
        if start >= end:
            raise ValueError("字幕结束时间必须晚于开始时间")
        previous = old.get(entry["index"])
        if previous and (previous["start_time"], previous["end_time"]) == (entry["start_time"], entry["end_time"]):
            continue
        if position and start < milliseconds(after[position - 1]["end_time"]):
            raise ValueError("时间调整与前一条字幕重叠")
        if position + 1 < len(after) and end > milliseconds(after[position + 1]["start_time"]):
            raise ValueError("时间调整与后一条字幕重叠")
