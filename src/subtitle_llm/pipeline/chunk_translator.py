from __future__ import annotations

import json
from dataclasses import dataclass

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.llm.types import ChatClient, CompletionUsage
from subtitle_llm.pipeline.llm_operations import LlmOperationRunner
from subtitle_llm.pipeline.llm_trace import LlmTraceRecorder
from subtitle_llm.pipeline.prompts import (
    ALIGNMENT_DRIFT_RETRANSLATE_PROMPT,
    FIX_MISSING_TRANSLATIONS_PROMPT,
    REFINE_TRANSLATION_PROMPT,
    REFINE_SEMANTIC_UNITS_PROMPT,
    REPAIR_SEMANTIC_TIMED_CUES_PROMPT,
    RE_TRANSLATE_PROMPT,
    SOURCE_CORRECTION_REPAIR_PROMPT,
    TRANSLATE_SEMANTIC_TIMED_CUES_PROMPT,
    TRANSLATE_SEMANTIC_UNITS_PROMPT,
    TRANSLATE_CHUNK_PROMPT,
)
from subtitle_llm.pipeline.semantic_units import (
    SemanticUnit,
    apply_semantic_translation,
    format_semantic_timed_cues_json,
    semantic_entries,
    semantic_unit_source_entries,
)
from subtitle_llm.pipeline.text import (
    extract_translation_block,
    format_indexed_translations,
    format_alignment_anchors,
    format_chunk,
    format_semantic_units_json,
    format_translation_reference,
    extract_json_payload,
    parse_indexed_translation_for_entries,
    process_semantic_json_translation,
    process_timed_cue_json_translation,
    process_translation,
)
from subtitle_llm.pipeline.source_corrections import SourceCorrectionFlag
from subtitle_llm.progress_contract import (
    chunk_stage_label,
)
from subtitle_llm.progress_events import ProgressEmitter
from subtitle_llm.settings import ModelConfig


@dataclass
class ChunkTranslationResult:
    chunk: list[SubtitleEntry]
    translation: str
    usage: CompletionUsage
    final_trace_id: str | None = None


@dataclass
class TracedTranslationText:
    text: str
    trace_id: str | None


class ChunkTranslator:
    def __init__(
        self,
        client: ChatClient,
        model_config: ModelConfig,
        trace_recorder: LlmTraceRecorder | None = None,
        total_chunks: int | None = None,
        progress: ProgressEmitter | None = None,
    ):
        self.client = client
        self.model_config = model_config
        self.trace_recorder = trace_recorder
        self.total_chunks = total_chunks
        self.progress = progress
        self.operations = LlmOperationRunner(
            client,
            model_config,
            trace_recorder=trace_recorder,
            total_chunks=total_chunks,
            progress=progress,
        )

    def translate_and_refine(
        self,
        chunk: list[SubtitleEntry],
        context: str,
        target_language: str,
        boundary_context: str,
        chunk_index: int | None = None,
        stage_prefix: str = "",
        refine_translation: bool = True,
    ) -> ChunkTranslationResult:
        usage = CompletionUsage()
        rough_translation = self.translate_chunk(
            chunk,
            context,
            target_language,
            boundary_context,
            usage,
            chunk_index=chunk_index,
            stage=join_stage(stage_prefix, "rough"),
        )
        if not refine_translation:
            return ChunkTranslationResult(
                chunk=chunk,
                translation=rough_translation.text,
                usage=usage,
                final_trace_id=rough_translation.trace_id,
            )

        refined_translation = self.refine_translation(
            chunk,
            rough_translation.text,
            context,
            target_language,
            boundary_context,
            usage,
            chunk_index=chunk_index,
            stage=join_stage(stage_prefix, "refine"),
        )
        return ChunkTranslationResult(
            chunk=chunk,
            translation=refined_translation.text,
            usage=usage,
            final_trace_id=refined_translation.trace_id,
        )

    def translate_semantic_and_refine(
        self,
        chunk: list[SubtitleEntry],
        context: str,
        target_language: str,
        boundary_context: str,
        chunk_index: int | None = None,
        stage_prefix: str = "",
        refine_translation: bool = True,
    ) -> ChunkTranslationResult:
        usage = CompletionUsage()
        rough_translation = self.translate_semantic_units(
            chunk,
            context,
            target_language,
            boundary_context,
            usage,
            chunk_index=chunk_index,
            stage=join_stage(stage_prefix, "semantic-rough"),
        )
        if not refine_translation:
            return ChunkTranslationResult(
                chunk=chunk,
                translation=rough_translation.text,
                usage=usage,
                final_trace_id=rough_translation.trace_id,
            )

        refined_translation = self.refine_semantic_units(
            chunk,
            rough_translation.text,
            context,
            target_language,
            boundary_context,
            usage,
            chunk_index=chunk_index,
            stage=join_stage(stage_prefix, "semantic-refine"),
        )
        return ChunkTranslationResult(
            chunk=chunk,
            translation=refined_translation.text,
            usage=usage,
            final_trace_id=refined_translation.trace_id,
        )

    def translate_semantic_timed_cues(
        self,
        units: list[SemanticUnit],
        context: str,
        target_language: str,
        boundary_context: str,
        chunk_index: int | None = None,
        stage: str = "semantic-cue-rough",
    ) -> ChunkTranslationResult:
        usage = CompletionUsage()
        cue_entries = semantic_unit_source_entries(units)
        unit_text = format_semantic_timed_cues_json(units)
        prompt = TRANSLATE_SEMANTIC_TIMED_CUES_PROMPT.format(
            target_language=target_language,
            context=context,
            boundary_context=boundary_context,
            unit_text=unit_text,
            semantic_unit_count=len(units),
            cue_count=len(cue_entries),
        )
        operation = self.operations.create_completion(prompt, stage=stage, chunk=cue_entries, chunk_index=chunk_index)
        result = operation.completion
        duration_ms = operation.duration_ms
        usage.add(result.usage)
        processed_translation = self._process_semantic_timed_response(
            result.content,
            units,
            cue_entries,
            target_language=target_language,
            stage=stage,
            prompt=prompt,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk_index=chunk_index,
        )
        trace_id = self.operations.record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=cue_entries,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self.operations.emit_chunk_progress(
            stage,
            "running",
            f"{chunk_stage_label(stage)}响应已解析",
            cue_entries,
            chunk_index,
            trace_id=trace_id,
        )
        return ChunkTranslationResult(
            chunk=cue_entries,
            translation=processed_translation,
            usage=usage,
            final_trace_id=trace_id,
        )

    def translate_semantic_units(
        self,
        chunk: list[SubtitleEntry],
        context: str,
        target_language: str,
        boundary_context: str,
        usage: CompletionUsage,
        chunk_index: int | None = None,
        stage: str = "semantic-rough",
    ) -> TracedTranslationText:
        unit_text = format_semantic_units_json(chunk)
        prompt = TRANSLATE_SEMANTIC_UNITS_PROMPT.format(
            target_language=target_language,
            context=context,
            boundary_context=boundary_context,
            unit_text=unit_text,
            chunk_size=len(chunk),
        )
        operation = self.operations.create_completion(prompt, stage=stage, chunk=chunk, chunk_index=chunk_index)
        result = operation.completion
        duration_ms = operation.duration_ms
        usage.add(result.usage)
        processed_translation = self._process_semantic_json_response(
            result.content,
            chunk,
            stage=stage,
            prompt=prompt,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk_index=chunk_index,
        )
        trace_id = self.operations.record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=chunk,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self.operations.emit_chunk_progress(
            stage,
            "running",
            f"{chunk_stage_label(stage)}响应已解析",
            chunk,
            chunk_index,
            trace_id=trace_id,
        )
        return TracedTranslationText(processed_translation, trace_id)

    def refine_semantic_units(
        self,
        chunk: list[SubtitleEntry],
        rough_translation: str,
        context: str,
        target_language: str,
        boundary_context: str,
        usage: CompletionUsage,
        chunk_index: int | None = None,
        stage: str = "semantic-refine",
    ) -> TracedTranslationText:
        unit_text = format_semantic_units_json(chunk)
        prompt = REFINE_SEMANTIC_UNITS_PROMPT.format(
            target_language=target_language,
            context=context,
            boundary_context=boundary_context,
            unit_text=unit_text,
            rough_translation=rough_translation,
            chunk_size=len(chunk),
        )
        operation = self.operations.create_completion(prompt, stage=stage, chunk=chunk, chunk_index=chunk_index)
        result = operation.completion
        duration_ms = operation.duration_ms
        usage.add(result.usage)
        processed_translation = self._process_semantic_json_response(
            result.content,
            chunk,
            stage=stage,
            prompt=prompt,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk_index=chunk_index,
        )
        trace_id = self.operations.record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=chunk,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self.operations.emit_chunk_progress(
            stage,
            "running",
            f"{chunk_stage_label(stage)}响应已解析",
            chunk,
            chunk_index,
            trace_id=trace_id,
        )
        return TracedTranslationText(processed_translation, trace_id)

    def translate_chunk(
        self,
        chunk: list[SubtitleEntry],
        context: str,
        target_language: str,
        boundary_context: str,
        usage: CompletionUsage,
        chunk_index: int | None = None,
        stage: str = "rough",
    ) -> TracedTranslationText:
        original_text = format_chunk(chunk)
        prompt = TRANSLATE_CHUNK_PROMPT.format(
            target_language=target_language,
            context=context,
            boundary_context=boundary_context,
            chunk_text=original_text,
            chunk_size=len(chunk),
        )
        operation = self.operations.create_completion(prompt, stage=stage, chunk=chunk, chunk_index=chunk_index)
        result = operation.completion
        duration_ms = operation.duration_ms
        usage.add(result.usage)
        processed_translation = process_translation(original_text, result.content, chunk)
        trace_id = self.operations.record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=chunk,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self.operations.emit_chunk_progress(
            stage,
            "running",
            f"{chunk_stage_label(stage)}响应已解析",
            chunk,
            chunk_index,
            trace_id=trace_id,
        )
        return TracedTranslationText(processed_translation, trace_id)

    def refine_translation(
        self,
        chunk: list[SubtitleEntry],
        rough_translation: str,
        context: str,
        target_language: str,
        boundary_context: str,
        usage: CompletionUsage,
        chunk_index: int | None = None,
        stage: str = "refine",
    ) -> TracedTranslationText:
        original_text = format_chunk(chunk)
        prompt = REFINE_TRANSLATION_PROMPT.format(
            target_language=target_language,
            context=context,
            boundary_context=boundary_context,
            original_text=original_text,
            rough_translation=rough_translation,
            chunk_size=len(chunk),
        )
        operation = self.operations.create_completion(prompt, stage=stage, chunk=chunk, chunk_index=chunk_index)
        result = operation.completion
        duration_ms = operation.duration_ms
        usage.add(result.usage)
        processed_translation = process_translation(original_text, result.content, chunk)
        trace_id = self.operations.record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=chunk,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self.operations.emit_chunk_progress(
            stage,
            "running",
            f"{chunk_stage_label(stage)}响应已解析",
            chunk,
            chunk_index,
            trace_id=trace_id,
        )
        return TracedTranslationText(processed_translation, trace_id)

    def repair_translation(
        self,
        chunk: list[SubtitleEntry],
        translation: str,
        target_language: str,
        usage: CompletionUsage,
        quality_report: str = "",
    ) -> str:
        return self.re_translate(chunk, translation, target_language, usage, quality_report=quality_report)

    def repair_translation_traced(
        self,
        chunk: list[SubtitleEntry],
        translation: str,
        target_language: str,
        usage: CompletionUsage,
        quality_report: str = "",
        chunk_index: int | None = None,
    ) -> TracedTranslationText:
        return self._re_translate_traced(
            chunk,
            translation,
            target_language,
            usage,
            quality_report=quality_report,
            chunk_index=chunk_index,
            stage="repair",
        )

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
        operation = self.operations.create_completion(prompt, stage="missing-fix", chunk=chunk)
        result = operation.completion
        duration_ms = operation.duration_ms
        usage.add(result.usage)
        self.operations.record_trace(
            stage="missing-fix",
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=chunk,
            processed_translation=result.content,
        )
        self.operations.emit_chunk_progress(
            "missing-fix",
            "repairing",
            "缺失翻译修复响应已解析",
            chunk,
            None,
            usage=result.usage,
            duration_ms=duration_ms,
        )
        return result.content

    def re_translate(
        self,
        chunk: list[SubtitleEntry],
        translation: str,
        target_language: str,
        usage: CompletionUsage,
        quality_report: str = "",
    ) -> str:
        return self._re_translate_traced(
            chunk,
            translation,
            target_language,
            usage,
            quality_report=quality_report,
        ).text

    def _re_translate_traced(
        self,
        chunk: list[SubtitleEntry],
        translation: str,
        target_language: str,
        usage: CompletionUsage,
        quality_report: str = "",
        chunk_index: int | None = None,
        stage: str = "repair",
    ) -> TracedTranslationText:
        original_text = format_chunk(chunk)
        prompt = RE_TRANSLATE_PROMPT.format(
            target_language=target_language,
            original_text=original_text,
            translation=translation,
            quality_report=quality_report or "No structured quality report was provided.",
            chunk_size=len(chunk),
        )
        operation = self.operations.create_completion(prompt, stage=stage, chunk=chunk, chunk_index=chunk_index)
        result = operation.completion
        duration_ms = operation.duration_ms
        usage.add(result.usage)
        extracted_response = extract_translation_block(result.content)
        processed_translation = process_translation(original_text, extracted_response, chunk)
        trace_id = self.operations.record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=chunk,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self.operations.emit_chunk_progress(
            stage,
            "repairing",
            f"{chunk_stage_label(stage)}响应已解析",
            chunk,
            chunk_index,
            trace_id=trace_id,
        )
        return TracedTranslationText(processed_translation, trace_id)

    def repair_semantic_timed_cues_traced(
        self,
        output_entries: list[SubtitleEntry],
        repair_brief: str,
        context: str,
        target_language: str,
        boundary_context: str,
        usage: CompletionUsage,
        chunk_index: int | None = None,
        stage: str = "tui-semantic-repair",
    ) -> TracedTranslationText:
        prompt = REPAIR_SEMANTIC_TIMED_CUES_PROMPT.format(
            target_language=target_language,
            context=context,
            boundary_context=boundary_context,
            repair_brief=repair_brief,
            chunk_size=len(output_entries),
        )
        operation = self.operations.create_completion(prompt, stage=stage, chunk=output_entries, chunk_index=chunk_index)
        result = operation.completion
        duration_ms = operation.duration_ms
        usage.add(result.usage)
        extracted_response = extract_translation_block(result.content)
        try:
            parsed_translations = parse_indexed_translation_for_entries(
                extracted_response,
                output_entries,
            )
        except Exception as exc:
            self.operations.record_trace(
                stage=stage,
                prompt=prompt,
                response=result.content,
                usage=result.usage,
                duration_ms=duration_ms,
                chunk=output_entries,
                chunk_index=chunk_index,
                processed_translation="",
                error=str(exc),
            )
            self.operations.emit_chunk_progress(
                stage,
                "failed",
                f"{chunk_stage_label(stage)}解析失败：{exc}",
                output_entries,
                chunk_index,
            )
            raise

        processed_translation = format_indexed_translations(parsed_translations)
        trace_id = self.operations.record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=output_entries,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self.operations.emit_chunk_progress(
            stage,
            "repairing",
            f"{chunk_stage_label(stage)}响应已解析",
            output_entries,
            chunk_index,
            trace_id=trace_id,
        )
        return TracedTranslationText(processed_translation, trace_id)

    def repair_source_correction_traced(
        self,
        entry: SubtitleEntry,
        flag: SourceCorrectionFlag,
        nearby_entries: list[SubtitleEntry],
        target_language: str,
        usage: CompletionUsage,
        chunk_index: int | None = None,
        stage: str = "source-correction-repair",
    ) -> TracedTranslationText:
        prompt = SOURCE_CORRECTION_REPAIR_PROMPT.format(
            target_language=target_language,
            cue_id=entry.index,
            correction=flag.to_prompt_text(),
            nearby_cues=format_source_correction_nearby_cues(nearby_entries),
        )
        operation = self.operations.create_completion(prompt, stage=stage, chunk=[entry], chunk_index=chunk_index)
        result = operation.completion
        duration_ms = operation.duration_ms
        usage.add(result.usage)
        try:
            payload = extract_json_payload(result.content)
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("source correction repair JSON must be an object")
            cue_id = int(data.get("cue_id", entry.index))
            if cue_id not in {entry.index, 1}:
                raise ValueError(f"source correction repair cue_id mismatch: expected={entry.index}, actual={cue_id}")
            translation = str(data.get("translation", "")).strip()
            if not translation:
                raise ValueError("source correction repair translation is empty")
        except Exception as exc:
            self.operations.record_trace(
                stage=stage,
                prompt=prompt,
                response=result.content,
                usage=result.usage,
                duration_ms=duration_ms,
                chunk=[entry],
                chunk_index=chunk_index,
                processed_translation="",
                error=str(exc),
            )
            self.operations.emit_chunk_progress(
                stage,
                "failed",
                f"{chunk_stage_label(stage)}解析失败：{exc}",
                [entry],
                chunk_index,
            )
            raise

        processed_translation = f"[{entry.index}]\n{translation}"
        trace_id = self.operations.record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=[entry],
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self.operations.emit_chunk_progress(
            stage,
            "repairing",
            f"{chunk_stage_label(stage)}响应已解析",
            [entry],
            chunk_index,
            trace_id=trace_id,
        )
        return TracedTranslationText(translation, trace_id)

    def retranslate_alignment_drift(
        self,
        drift_chunk: list[SubtitleEntry],
        stable_anchors: list[SubtitleEntry],
        context: str,
        target_language: str,
        boundary_context: str,
        usage: CompletionUsage,
    ) -> str:
        return self.retranslate_alignment_drift_traced(
            drift_chunk,
            stable_anchors,
            context,
            target_language,
            boundary_context,
            usage,
        ).text

    def retranslate_alignment_drift_traced(
        self,
        drift_chunk: list[SubtitleEntry],
        stable_anchors: list[SubtitleEntry],
        context: str,
        target_language: str,
        boundary_context: str,
        usage: CompletionUsage,
        chunk_index: int | None = None,
    ) -> TracedTranslationText:
        original_text = format_chunk(drift_chunk)
        prompt = ALIGNMENT_DRIFT_RETRANSLATE_PROMPT.format(
            target_language=target_language,
            context=context,
            stable_anchors=format_alignment_anchors(stable_anchors),
            boundary_context=boundary_context,
            original_text=original_text,
            translation_reference=format_translation_reference(drift_chunk),
            chunk_size=len(drift_chunk),
        )
        operation = self.operations.create_completion(prompt, stage="drift", chunk=drift_chunk, chunk_index=chunk_index)
        result = operation.completion
        duration_ms = operation.duration_ms
        usage.add(result.usage)
        extracted_response = extract_translation_block(result.content)
        processed_translation = process_translation(original_text, extracted_response, drift_chunk)
        trace_id = self.operations.record_trace(
            stage="drift",
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=drift_chunk,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self.operations.emit_chunk_progress(
            "drift",
            "repairing",
            "对齐漂移重译响应已解析",
            drift_chunk,
            chunk_index,
            trace_id=trace_id,
        )
        return TracedTranslationText(processed_translation, trace_id)

    def _process_semantic_json_response(
        self,
        response: str,
        chunk: list[SubtitleEntry],
        *,
        stage: str,
        prompt: str,
        usage: CompletionUsage,
        duration_ms: int,
        chunk_index: int | None,
    ) -> str:
        try:
            return process_semantic_json_translation(response, chunk)
        except Exception as exc:
            self.operations.record_trace(
                stage=stage,
                prompt=prompt,
                response=response,
                usage=usage,
                duration_ms=duration_ms,
                chunk=chunk,
                chunk_index=chunk_index,
                processed_translation="",
                error=str(exc),
            )
            raise

    def _process_semantic_timed_response(
        self,
        response: str,
        units: list[SemanticUnit],
        cue_entries: list[SubtitleEntry],
        *,
        target_language: str,
        stage: str,
        prompt: str,
        usage: CompletionUsage,
        duration_ms: int,
        chunk_index: int | None,
    ) -> str:
        try:
            return process_timed_cue_json_translation(response, cue_entries)
        except Exception as timed_exc:
            unit_entries = semantic_entries(units)
            try:
                process_semantic_json_translation(response, unit_entries)
                for unit_entry, unit in zip(unit_entries, units, strict=True):
                    apply_semantic_translation(
                        unit,
                        unit_entry.translated_text,
                        target_language=target_language,
                    )
                return format_indexed_translations([entry.translated_text for entry in cue_entries])
            except Exception as semantic_exc:
                self.operations.record_trace(
                    stage=stage,
                    prompt=prompt,
                    response=response,
                    usage=usage,
                    duration_ms=duration_ms,
                    chunk=cue_entries,
                    chunk_index=chunk_index,
                    processed_translation="",
                    error=f"timed cue parse failed: {timed_exc}; semantic fallback failed: {semantic_exc}",
                )
                raise timed_exc

def join_stage(prefix: str, stage: str) -> str:
    return f"{prefix}-{stage}" if prefix else stage


def format_source_correction_nearby_cues(entries: list[SubtitleEntry]) -> str:
    if not entries:
        return "(none)"
    return "\n\n".join(
        (
            f"[{entry.index}]\n"
            f"Source: {entry.original_text}\n"
            f"Current translation: {entry.translated_text or '(empty)'}"
        )
        for entry in entries
    )
