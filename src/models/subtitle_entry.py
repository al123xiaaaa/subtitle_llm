class SubtitleEntry:
    def __init__(self, index, start_time, end_time, text):
        self.index = index
        self.start_time = start_time
        self.end_time = end_time
        self.original_text = text
        self.translated_text = ""

    def set_translated_text(self, translated_text):
        self.translated_text = translated_text

    def get_bilingual_text(self):
        return f"{self.original_text}\n{self.translated_text}"

    def __str__(self):
        return f"{self.index}\n{self.start_time} --> {self.end_time}\n{self.get_bilingual_text()}"