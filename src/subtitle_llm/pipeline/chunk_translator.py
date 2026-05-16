from __future__ import annotations

from dataclasses import dataclass

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.llm.types import ChatClient, CompletionUsage
from subtitle_llm.pipeline.prompts import (
    FIX_MISSING_TRANSLATIONS_PROMPT,
    REFINE_TRANSLATION_PROMPT,
    RE_TRANSLATE_PROMPT,
    TRANSLATE_CHUNK_PROMPT,
)
from subtitle_llm.pipeline.text import (
    extract_translation_block,
    format_chunk,
    process_translation,
)
from subtitle_llm.settings import ModelConfig


@dataclass
class ChunkTranslationResult:
    chunk: list[SubtitleEntry]
    translation: str
    usage: CompletionUsage


class ChunkTranslator:
    def __init__(self, client: ChatClient, model_config: ModelConfig):
        self.client = client
        self.model_config = model_config

    def translate_and_refine(
        self,
        chunk: list[SubtitleEntry],
        context: str,
        target_language: str,
        boundary_context: str,
    ) -> ChunkTranslationResult:
        usage = CompletionUsage()
        rough_translation = self.translate_chunk(chunk, context, target_language, boundary_context, usage)
        refined_translation = self.refine_translation(chunk, rough_translation, context, target_language, boundary_context, usage)
        return ChunkTranslationResult(chunk=chunk, translation=refined_translation, usage=usage)

    def translate_chunk(
        self,
        chunk: list[SubtitleEntry],
        context: str,
        target_language: str,
        boundary_context: str,
        usage: CompletionUsage,
    ) -> str:
        original_text = format_chunk(chunk)
        prompt = TRANSLATE_CHUNK_PROMPT.format(
            target_language=target_language,
            context=context,
            boundary_context=boundary_context,
            chunk_text=original_text,
            chunk_size=len(chunk),
        )
        result = self.client.create_completion(self.model_config, [{"role": "user", "content": prompt}])
        usage.add(result.usage)
        return process_translation(original_text, result.content, chunk)

    def refine_translation(
        self,
        chunk: list[SubtitleEntry],
        rough_translation: str,
        context: str,
        target_language: str,
        boundary_context: str,
        usage: CompletionUsage,
    ) -> str:
        original_text = format_chunk(chunk)
        prompt = REFINE_TRANSLATION_PROMPT.format(
            target_language=target_language,
            context=context,
            boundary_context=boundary_context,
            original_text=original_text,
            rough_translation=rough_translation,
            chunk_size=len(chunk),
        )
        result = self.client.create_completion(self.model_config, [{"role": "user", "content": prompt}])
        usage.add(result.usage)
        return process_translation(original_text, result.content, chunk)

    def repair_translation(
        self,
        chunk: list[SubtitleEntry],
        translation: str,
        target_language: str,
        usage: CompletionUsage,
        quality_report: str = "",
    ) -> str:
        return self.re_translate(chunk, translation, target_language, usage, quality_report=quality_report)

    def fix_missing_translations(
        self,
        chunk: list[SubtitleEntry],
        processed_lines: str,
        target_language: str,
        usage: CompletionUsage,
    ) -> str:
        original_text = format_chunk(chunk)
        missing_lines: dict[str, str] = {}
        processed_lines_list = processed_lines.split("\n")

        for i in range(0, len(processed_lines_list), 2):
            if i + 1 < len(processed_lines_list):
                index = processed_lines_list[i].strip("[]")
                if "Translation missing line" in processed_lines_list[i + 1]:
                    missing_lines[index] = processed_lines_list[i + 1].strip("[]")

        if not missing_lines:
            return processed_lines

        if len(missing_lines) == 1:
            missing_index = next(iter(missing_lines))
            example_format = f"Example of the required format:\n[{missing_index}]\n[Translated text for entry {missing_index}]\n"
        else:
            minimum = min(missing_lines)
            maximum = max(missing_lines)
            example_format = (
                f"Example of the required format(index from [{minimum}] to [{maximum}]):\n"
                f"[{minimum}]\n[Translated text for entry {minimum}]\n...\n"
                f"[{maximum}]\n[Translated text for entry {maximum}]\n"
            )

        missing_lines_formatted = "\n".join(
            [f"[{index}]\n[{line}]" for index, line in missing_lines.items()]
        )
        prompt = FIX_MISSING_TRANSLATIONS_PROMPT.format(
            target_language=target_language,
            original_text=original_text,
            missing_lines_indices=", ".join([str(index) for index in missing_lines]),
            missing_lines_formatted=missing_lines_formatted,
            example_format=example_format,
        )
        result = self.client.create_completion(self.model_config, [{"role": "user", "content": prompt}])
        usage.add(result.usage)
        return result.content

    def re_translate(
        self,
        chunk: list[SubtitleEntry],
        translation: str,
        target_language: str,
        usage: CompletionUsage,
        quality_report: str = "",
    ) -> str:
        original_text = format_chunk(chunk)
        prompt = RE_TRANSLATE_PROMPT.format(
            target_language=target_language,
            original_text=original_text,
            translation=translation,
            quality_report=quality_report or "No structured quality report was provided.",
            chunk_size=len(chunk),
        )
        result = self.client.create_completion(self.model_config, [{"role": "user", "content": prompt}])
        usage.add(result.usage)
        return process_translation(original_text, extract_translation_block(result.content), chunk)
