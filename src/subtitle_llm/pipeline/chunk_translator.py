from __future__ import annotations

import time
from dataclasses import dataclass

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.llm.types import ChatClient, CompletionResult, CompletionUsage
from subtitle_llm.pipeline.llm_trace import LlmTraceRecorder
from subtitle_llm.pipeline.prompts import (
    ALIGNMENT_DRIFT_RETRANSLATE_PROMPT,
    FIX_MISSING_TRANSLATIONS_PROMPT,
    REFINE_TRANSLATION_PROMPT,
    REFINE_SEMANTIC_UNITS_PROMPT,
    RE_TRANSLATE_PROMPT,
    TRANSLATE_SEMANTIC_UNITS_PROMPT,
    TRANSLATE_CHUNK_PROMPT,
)
from subtitle_llm.pipeline.text import (
    extract_translation_block,
    format_alignment_anchors,
    format_chunk,
    format_semantic_units_json,
    format_translation_reference,
    process_semantic_json_translation,
    process_translation,
)
from subtitle_llm.progress_events import ProgressEmitter, chunk_payload
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

    def translate_and_refine(
        self,
        chunk: list[SubtitleEntry],
        context: str,
        target_language: str,
        boundary_context: str,
        chunk_index: int | None = None,
        stage_prefix: str = "",
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
        result, duration_ms = self._create_completion(prompt, stage=stage, chunk=chunk, chunk_index=chunk_index)
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
        trace_id = self._record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=chunk,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self._emit_chunk_progress(
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
        result, duration_ms = self._create_completion(prompt, stage=stage, chunk=chunk, chunk_index=chunk_index)
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
        trace_id = self._record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=chunk,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self._emit_chunk_progress(
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
        result, duration_ms = self._create_completion(prompt, stage=stage, chunk=chunk, chunk_index=chunk_index)
        usage.add(result.usage)
        processed_translation = process_translation(original_text, result.content, chunk)
        trace_id = self._record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=chunk,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self._emit_chunk_progress(
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
        result, duration_ms = self._create_completion(prompt, stage=stage, chunk=chunk, chunk_index=chunk_index)
        usage.add(result.usage)
        processed_translation = process_translation(original_text, result.content, chunk)
        trace_id = self._record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=chunk,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self._emit_chunk_progress(
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
        result, duration_ms = self._create_completion(prompt, stage="missing-fix", chunk=chunk)
        usage.add(result.usage)
        self._record_trace(
            stage="missing-fix",
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=chunk,
            processed_translation=result.content,
        )
        self._emit_chunk_progress(
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
        result, duration_ms = self._create_completion(prompt, stage=stage, chunk=chunk, chunk_index=chunk_index)
        usage.add(result.usage)
        extracted_response = extract_translation_block(result.content)
        processed_translation = process_translation(original_text, extracted_response, chunk)
        trace_id = self._record_trace(
            stage=stage,
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=chunk,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self._emit_chunk_progress(
            stage,
            "repairing",
            f"{chunk_stage_label(stage)}响应已解析",
            chunk,
            chunk_index,
            trace_id=trace_id,
        )
        return TracedTranslationText(processed_translation, trace_id)

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
        result, duration_ms = self._create_completion(prompt, stage="drift", chunk=drift_chunk, chunk_index=chunk_index)
        usage.add(result.usage)
        extracted_response = extract_translation_block(result.content)
        processed_translation = process_translation(original_text, extracted_response, drift_chunk)
        trace_id = self._record_trace(
            stage="drift",
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=duration_ms,
            chunk=drift_chunk,
            chunk_index=chunk_index,
            processed_translation=processed_translation,
        )
        self._emit_chunk_progress(
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
            self._record_trace(
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

    def _create_completion(
        self,
        prompt: str,
        *,
        stage: str,
        chunk: list[SubtitleEntry] | None = None,
        chunk_index: int | None = None,
    ) -> tuple[CompletionResult, int]:
        started_at = time.perf_counter()
        self._emit_chunk_progress(
            stage,
            chunk_visual_status(stage),
            f"正在{chunk_stage_label(stage)}"
            + chunk_position_text(chunk_index, self.total_chunks),
            chunk,
            chunk_index,
        )
        try:
            result = self.client.create_completion(self.model_config, [{"role": "user", "content": prompt}])
        except Exception as exc:
            duration_ms = elapsed_ms(started_at)
            self._emit_chunk_progress(
                stage,
                "failed",
                f"{chunk_stage_label(stage)}失败：{exc}",
                chunk,
                chunk_index,
                duration_ms=duration_ms,
            )
            if self.trace_recorder is not None:
                self.trace_recorder.record_call(
                    stage="llm-error",
                    prompt=prompt,
                    response="",
                    model_config=self.model_config,
                    usage=CompletionUsage(),
                    duration_ms=duration_ms,
                    chunk=chunk,
                    chunk_index=chunk_index,
                    total_chunks=self.total_chunks,
                    expected_count=len(chunk) if chunk is not None else None,
                    status="failed",
                    error=str(exc),
                )
            raise
        duration_ms = elapsed_ms(started_at)
        self._emit_chunk_progress(
            stage,
            chunk_visual_status(stage),
            f"{chunk_stage_label(stage)}返回，耗时 {duration_ms / 1000:.1f}s",
            chunk,
            chunk_index,
            usage=result.usage,
            duration_ms=duration_ms,
        )
        return result, duration_ms

    def _record_trace(
        self,
        *,
        stage: str,
        prompt: str,
        response: str,
        usage: CompletionUsage,
        duration_ms: int,
        chunk: list[SubtitleEntry],
        chunk_index: int | None = None,
        processed_translation: str | None = None,
        error: str | None = None,
    ) -> str | None:
        if self.trace_recorder is None:
            return None
        return self.trace_recorder.record_call(
            stage=stage,
            prompt=prompt,
            response=response,
            model_config=self.model_config,
            usage=usage,
            duration_ms=duration_ms,
            chunk=chunk,
            chunk_index=chunk_index,
            total_chunks=self.total_chunks,
            processed_translation=processed_translation,
            expected_count=len(chunk),
            error=error,
        )

    def _emit_chunk_progress(
        self,
        detail: str,
        visual_status: str,
        message: str,
        chunk: list[SubtitleEntry] | None,
        chunk_index: int | None,
        *,
        usage: CompletionUsage | None = None,
        duration_ms: int | None = None,
        trace_id: str | None = None,
    ) -> None:
        if self.progress is None or chunk is None:
            return
        self.progress.emit(
            stage="processing_chunks",
            detail=detail_key(detail),
            status="running" if visual_status != "failed" else "failed",
            label=chunk_stage_label(detail),
            message=message,
            chunk=chunk_payload(
                chunk,
                chunk_index=chunk_index,
                total_chunks=self.total_chunks,
                status=visual_status,
                detail=detail_key(detail),
            ),
            model=self.model_config,
            usage=usage,
            trace_id=trace_id,
            duration_ms=duration_ms,
        )


def elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def join_stage(prefix: str, stage: str) -> str:
    return f"{prefix}-{stage}" if prefix else stage


def detail_key(stage: str) -> str:
    return stage.replace("-", "_")


def chunk_stage_label(stage: str) -> str:
    normalized = detail_key(stage)
    labels = {
        "rough": "初译",
        "refine": "润色",
        "semantic_rough": "语义初译",
        "semantic_refine": "语义润色",
        "tui_semantic": "TUI 语义重译",
        "tui_semantic_semantic_rough": "TUI 语义重译初译",
        "tui_semantic_semantic_refine": "TUI 语义重译润色",
        "repair": "自动修复",
        "missing_fix": "补齐缺失翻译",
        "drift": "对齐漂移重译",
        "tui_ordinary_rough": "TUI 普通重译初译",
        "tui_ordinary_refine": "TUI 普通重译润色",
        "llm_error": "模型请求",
    }
    return labels.get(normalized, normalized.replace("_", " "))


def chunk_visual_status(stage: str) -> str:
    normalized = detail_key(stage)
    if "repair" in normalized or "drift" in normalized or "tui" in normalized or "missing_fix" in normalized:
        return "repairing"
    return "running"


def chunk_position_text(chunk_index: int | None, total_chunks: int | None) -> str:
    if chunk_index is None:
        return ""
    if total_chunks:
        return f"第 {chunk_index + 1}/{total_chunks} 个片段"
    return f"第 {chunk_index + 1} 个片段"
