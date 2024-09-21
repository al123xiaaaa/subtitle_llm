class SubtitleEntry:
    def __init__(self, index, start_time, end_time, text):
        self.index = index
        self.start_time = start_time
        self.end_time = end_time
        self.original_text = text
        self.translated_text = ""
        self.needs_retranslation = False  # New attribute with default value False

    def set_translated_text(self, translated_text):
        self.translated_text = translated_text

    def get_bilingual_text(self):
        return f"{self.original_text}\n{self.translated_text}"

    def __str__(self):
        return f"{self.index}\n{self.start_time} --> {self.end_time}\n{self.get_bilingual_text()}"

    def to_dict(self):
        """Serializes the SubtitleEntry instance to a dictionary."""
        return {
            "index": self.index,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "original_text": self.original_text,
            "translated_text": self.translated_text,
            "needs_retranslation": self.needs_retranslation,
        }

    @classmethod
    def from_dict(cls, data):
        """Deserializes a dictionary to a SubtitleEntry instance."""
        entry = cls(
            index=data["index"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            text=data["original_text"],
        )
        entry.translated_text = data.get("translated_text", "")
        entry.needs_retranslation = data.get("needs_retranslation", False)
        return entry

    def set_needs_retranslation(self, value):
        self.needs_retranslation = value
        return self
