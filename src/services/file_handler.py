import os
from src.models.subtitle import Subtitle
from src.models.subtitle_entry import SubtitleEntry
import chardet


class FileHandler:
    @staticmethod
    def read_srt(file_path: str) -> Subtitle:
        # 创建一个新的字幕对象
        subtitle = Subtitle()
        # 定义可能的编码列表
        encodings = ["utf-8", "utf-16", "iso-8859-1", "windows-1252"]

        # 尝试使用不同的编码打开文件
        for encoding in encodings:
            try:
                with open(file_path, "r", encoding=encoding) as file:
                    lines = file.readlines()
                    break
            except UnicodeDecodeError:
                continue
        else:
            # 如果所有编码都失败，尝试检测编码
            with open(file_path, "rb") as file:
                raw_data = file.read()
            detected = chardet.detect(raw_data)
            encoding = detected["encoding"]
            lines = raw_data.decode(encoding).splitlines()

        def parse_entries(lines):
            entry = []
            for line in lines:
                line = line.strip()
                if not line and entry:
                    yield entry
                    entry = []
                elif line:
                    entry.append(line)
            if entry:
                yield entry

        def is_timecode(line):
            return (
                " --> " in line
                and line.replace(":", "")
                .replace(",", "")
                .replace(" --> ", "")
                .isdigit()
            )

        for entry in parse_entries(lines):
            if len(entry) >= 3 and is_timecode(entry[1]):
                try:
                    index = int(entry[0])
                    start, end = entry[1].split(" --> ")
                    text = "\n".join(entry[2:])
                    subtitle.add_entry(SubtitleEntry(index, start, end, text))
                except ValueError:
                    # 如果第一行不是有效的索引，将整个条目视为文本
                    text = "\n".join(entry)
                    if subtitle.entries:
                        subtitle.entries[-1].text += "\n" + text
            elif subtitle.entries:
                # 如果不是有效的字幕条目，将其添加到前一个条目的文本中
                subtitle.entries[-1].text += "\n" + "\n".join(entry)

        return subtitle

    @staticmethod
    def write_srt(subtitle, output_file, output_format="source-first"):
        FileHandler.ensure_directory(output_file)
        output_format = {
            "bilingual": "source-first",
            "source-first": "source-first",
            "target-first": "target-first",
            "target-only": "target-only",
            "source-only": "source-only",
        }.get(output_format, output_format)

        with open(output_file, "w", encoding="utf-8") as file:
            for entry in subtitle.entries:
                if output_format == "source-first":
                    text = f"{entry.original_text}\n{entry.translated_text}"
                elif output_format == "target-first":
                    text = f"{entry.translated_text}\n{entry.original_text}"
                elif output_format == "target-only":
                    text = entry.translated_text
                elif output_format == "source-only":
                    text = entry.original_text
                else:
                    raise ValueError(f"Unsupported output format: {output_format}")

                file.write(
                    f"{entry.index}\n"
                    f"{entry.start_time} --> {entry.end_time}\n"
                    f"{text}\n\n"
                )

    @staticmethod
    def ensure_directory(file_path: str):
        # 确保文件路径的目录存在，如果不存在则创建
        directory = os.path.dirname(file_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
