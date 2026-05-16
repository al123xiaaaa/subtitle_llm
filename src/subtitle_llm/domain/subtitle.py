from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SubtitleEntry:
    index: int
    start_time: str
    end_time: str
    original_text: str
    translated_text: str = ""
    needs_retranslation: bool = False

    @property
    def text(self) -> str:
        return self.original_text

    @text.setter
    def text(self, value: str) -> None:
        self.original_text = value

    def set_translated_text(self, translated_text: str) -> "SubtitleEntry":
        self.translated_text = translated_text
        return self

    def set_needs_retranslation(self, value: bool) -> "SubtitleEntry":
        self.needs_retranslation = value
        return self

    def get_bilingual_text(self) -> str:
        return f"{self.original_text}\n{self.translated_text}"

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "original_text": self.original_text,
            "translated_text": self.translated_text,
            "needs_retranslation": self.needs_retranslation,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SubtitleEntry":
        return cls(
            index=int(data["index"]),
            start_time=data["start_time"],
            end_time=data["end_time"],
            original_text=data.get("original_text", data.get("text", "")),
            translated_text=data.get("translated_text", ""),
            needs_retranslation=bool(data.get("needs_retranslation", False)),
        )

    def __str__(self) -> str:
        return (
            f"{self.index}\n"
            f"{self.start_time} --> {self.end_time}\n"
            f"{self.get_bilingual_text()}"
        )


@dataclass
class Subtitle:
    entries: list[SubtitleEntry] = field(default_factory=list)

    def add_entry(self, entry: SubtitleEntry) -> None:
        if not isinstance(entry, SubtitleEntry):
            raise TypeError("entry must be a SubtitleEntry")
        self.entries.append(entry)

    def reorder_entries(self) -> None:
        for index, entry in enumerate(self.entries, start=1):
            entry.index = index

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __str__(self) -> str:
        return "\n\n".join(str(entry) for entry in self.entries)
