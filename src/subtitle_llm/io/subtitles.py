from __future__ import annotations

import json
import math
import re
from pathlib import Path

import chardet

from subtitle_llm.domain import Segment, Subtitle, SubtitleEntry, Transcript, Word


def seconds_to_srt_time(seconds: float) -> str:
    millis = int(round((seconds - math.floor(seconds)) * 1000))
    seconds = math.floor(seconds)
    mins, sec = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    return f"{hrs:02}:{mins:02}:{sec:02},{millis:03}"


def count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


class SubtitleIO:
    @staticmethod
    def read(path: str | Path, max_chars: int = 132, max_duration: float = 10.0) -> Subtitle:
        file_path = Path(path)
        suffix = file_path.suffix.lower()
        if suffix == ".srt":
            return SubtitleIO.read_srt(file_path)
        if suffix == ".json":
            return JSONSubtitleReader(max_chars=max_chars, max_duration=max_duration).read(file_path)
        raise ValueError(f"Unsupported input format: {suffix}")

    @staticmethod
    def read_srt(file_path: str | Path) -> Subtitle:
        path = Path(file_path)
        encodings = ["utf-8", "utf-16", "iso-8859-1", "windows-1252"]

        lines: list[str] | None = None
        for encoding in encodings:
            try:
                lines = path.read_text(encoding=encoding).splitlines()
                break
            except UnicodeDecodeError:
                continue

        if lines is None:
            raw_data = path.read_bytes()
            detected = chardet.detect(raw_data)
            encoding = detected["encoding"] or "utf-8"
            lines = raw_data.decode(encoding).splitlines()

        subtitle = Subtitle()

        def parse_entries(source_lines: list[str]):
            entry: list[str] = []
            for raw_line in source_lines:
                line = raw_line.strip()
                if not line and entry:
                    yield entry
                    entry = []
                elif line:
                    entry.append(line)
            if entry:
                yield entry

        def is_timecode(line: str) -> bool:
            return (
                " --> " in line
                and line.replace(":", "").replace(",", "").replace(" --> ", "").isdigit()
            )

        for entry in parse_entries(lines):
            if len(entry) >= 3 and is_timecode(entry[1]):
                try:
                    index = int(entry[0])
                    start, end = entry[1].split(" --> ")
                    subtitle.add_entry(SubtitleEntry(index, start, end, "\n".join(entry[2:])))
                except ValueError:
                    if subtitle.entries:
                        subtitle.entries[-1].original_text += "\n" + "\n".join(entry)
            elif subtitle.entries:
                subtitle.entries[-1].original_text += "\n" + "\n".join(entry)

        return subtitle

    @staticmethod
    def write_srt(subtitle: Subtitle, output_file: str | Path, output_format: str = "source-first") -> None:
        path = Path(output_file)
        if path.parent:
            path.parent.mkdir(parents=True, exist_ok=True)

        output_format = {
            "bilingual": "source-first",
            "source-first": "source-first",
            "target-first": "target-first",
            "target-only": "target-only",
            "source-only": "source-only",
        }.get(output_format, output_format)

        with path.open("w", encoding="utf-8") as file:
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


class JSONSubtitleReader:
    def __init__(self, max_chars: int = 132, max_duration: float = 10.0):
        self.max_chars = max_chars
        self.max_duration = max_duration
        self.split_punctuations = {".", "!", "?"}

    def read(self, file_path: str | Path) -> Subtitle:
        transcript = self.read_json(file_path)
        return self.split_transcript(transcript)

    def read_json(self, file_path: str | Path) -> Transcript:
        data = json.loads(Path(file_path).read_text(encoding="utf-8"))
        segments: list[Segment] = []

        for segment_data in data.get("segments", []):
            words: list[Word] = []
            previous_word: Word | None = None
            for word_data in segment_data.get("words", []):
                if "start" not in word_data or "end" not in word_data or "score" not in word_data:
                    if previous_word:
                        word_data["start"] = previous_word.start + 0.3
                        word_data["end"] = previous_word.end + 0.5
                        word_data["score"] = previous_word.score
                    else:
                        word_data["start"] = segment_data.get("start", 0.0)
                        word_data["end"] = segment_data.get("end", 0.0)
                        word_data["score"] = 0.0
                word = Word(**word_data)
                words.append(word)
                previous_word = word

            segments.append(
                Segment(
                    start=segment_data.get("start", 0.0),
                    end=segment_data.get("end", 0.0),
                    text=segment_data.get("text", ""),
                    words=words,
                )
            )

        return Transcript(segments=segments)

    def split_transcript(self, transcript: Transcript) -> Subtitle:
        subtitle = Subtitle()
        buffer_words: list[Word] = []
        buffer_start: float | None = None
        buffer_end: float | None = None

        for segment in transcript.segments:
            for word in segment.words:
                if not buffer_words:
                    buffer_start = word.start
                buffer_words.append(word)
                buffer_end = word.end

                buffer_text = " ".join([w.word for w in buffer_words]).strip()
                buffer_duration = buffer_end - (buffer_start or 0.0)
                is_end_punctuation = buffer_words[-1].word.endswith(tuple(self.split_punctuations))
                exceeds_chars = len(buffer_text) > self.max_chars
                exceeds_duration = buffer_duration > self.max_duration

                if exceeds_chars or exceeds_duration or is_end_punctuation:
                    split_index = self._choose_split_index(buffer_words, exceeds_chars, exceeds_duration)
                    sub_words = buffer_words[:split_index]
                    if sub_words:
                        subtitle.add_entry(
                            SubtitleEntry(
                                index=len(subtitle.entries) + 1,
                                start_time=seconds_to_srt_time(buffer_start or 0.0),
                                end_time=seconds_to_srt_time(sub_words[-1].end),
                                original_text=" ".join([w.word for w in sub_words]).strip(),
                            )
                        )
                    buffer_words = buffer_words[split_index:]
                    buffer_start = buffer_words[0].start if buffer_words else None
                    buffer_end = buffer_words[-1].end if buffer_words else None

        if buffer_words:
            subtitle.add_entry(
                SubtitleEntry(
                    index=len(subtitle.entries) + 1,
                    start_time=seconds_to_srt_time(buffer_start or 0.0),
                    end_time=seconds_to_srt_time(buffer_end or 0.0),
                    original_text=" ".join([w.word for w in buffer_words]).strip(),
                )
            )

        self.merge_short_subtitles(subtitle)
        subtitle.reorder_entries()
        return subtitle

    def _choose_split_index(self, words: list[Word], exceeds_chars: bool, exceeds_duration: bool) -> int:
        if words[-1].word.endswith(tuple(self.split_punctuations)):
            return len(words)
        for index in range(len(words) - 1, -1, -1):
            if words[index].word.endswith(tuple(self.split_punctuations)):
                return index + 1
        if exceeds_chars or exceeds_duration:
            return max(1, len(words) - 1)
        return len(words)

    def merge_short_subtitles(self, subtitle: Subtitle, max_words: int = 3) -> None:
        optimized_entries: list[SubtitleEntry] = []
        for entry in subtitle.entries:
            if count_words(entry.original_text) <= max_words and optimized_entries:
                optimized_entries[-1].original_text += " " + entry.original_text
                optimized_entries[-1].end_time = entry.end_time
            else:
                optimized_entries.append(entry)
        subtitle.entries = optimized_entries
