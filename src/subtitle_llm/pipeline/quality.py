from __future__ import annotations

from subtitle_llm.domain import SubtitleEntry


class QualityGate:
    def mark_entries_for_retranslation(self, chunk: list[SubtitleEntry]) -> bool:
        for entry in chunk:
            if self.needs_retranslation(entry):
                entry.needs_retranslation = True
        return any(entry.needs_retranslation for entry in chunk)

    def needs_retranslation(self, entry: SubtitleEntry) -> bool:
        translated = entry.translated_text.strip()
        original = entry.original_text
        return (
            translated == ""
            or "Translation missing line" in translated
            or "Translated text" in translated
            or "翻译缺失" in translated
            or (len(translated) - 2 < 0.1 * len(original) and len(original) > 26)
            or (len(translated) < 0.19 * len(original) and len(original) > 100)
            or (len(translated) > 0.65 * len(original) and len(original) > 26)
            or (bool(translated) and all(char in "，。？！：；\"、" for char in translated))
            or (bool(translated) and all(char in ",.?!:;\"'()-" for char in translated))
        )
