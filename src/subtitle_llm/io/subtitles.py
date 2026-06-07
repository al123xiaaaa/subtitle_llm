from __future__ import annotations

import json
import math
import re
from pathlib import Path

import chardet
import pysubs2

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
        subtitle = Subtitle()
        subtitles = load_srt_with_encoding_detection(path)
        for index, event in enumerate(subtitles, start=1):
            subtitle.add_entry(
                SubtitleEntry(
                    index=index,
                    start_time=ms_to_srt_time(event.start),
                    end_time=ms_to_srt_time(event.end),
                    original_text=pysubs2_text_to_srt_text(event.text),
                )
            )

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

        subtitles = pysubs2.SSAFile()
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

            subtitles.append(
                pysubs2.SSAEvent(
                    start=srt_time_to_ms(entry.start_time),
                    end=srt_time_to_ms(entry.end_time),
                    text=srt_text_to_pysubs2_text(text),
                )
            )
        subtitles.save(str(path), encoding="utf-8", format_="srt")


def load_srt_with_encoding_detection(path: Path) -> pysubs2.SSAFile:
    last_error: UnicodeDecodeError | None = None
    for encoding in candidate_encodings(path):
        try:
            return pysubs2.load(
                str(path),
                encoding=encoding,
                format_="srt",
                keep_unknown_html_tags=True,
            )
        except UnicodeDecodeError as error:
            last_error = error
    if last_error:
        raise last_error
    return pysubs2.SSAFile()


def candidate_encodings(path: Path) -> list[str]:
    encodings = ["utf-8", "utf-8-sig", "utf-16"]
    raw_data = path.read_bytes()
    detected = chardet.detect(raw_data).get("encoding")
    if detected and detected not in encodings:
        encodings.append(detected)
    encodings.extend(["iso-8859-1", "windows-1252"])
    return encodings


def pysubs2_text_to_srt_text(text: str) -> str:
    return text.replace("\\N", "\n").strip()


def srt_text_to_pysubs2_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\N")


def srt_time_to_ms(value: str) -> int:
    hours, minutes, seconds_ms = value.split(":")
    seconds, millis = seconds_ms.split(",")
    return (
        int(hours) * 60 * 60 * 1000
        + int(minutes) * 60 * 1000
        + int(seconds) * 1000
        + int(millis)
    )


def ms_to_srt_time(value: int) -> str:
    value = max(0, value)
    hours, remainder = divmod(value, 60 * 60 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


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
