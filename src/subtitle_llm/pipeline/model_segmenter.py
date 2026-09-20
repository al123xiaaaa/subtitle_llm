"""一次断句翻译、只修缺失范围，并持久保存已生成/已复核结果。"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.llm.types import CompletionUsage
from subtitle_llm.pipeline.chunk_translator import ChunkTranslationResult, ChunkTranslator
from subtitle_llm.pipeline.chunks import PlannedChunk
from subtitle_llm.pipeline.llm_trace import SourceCoverage
from subtitle_llm.pipeline.model_segmentation import (
    Piece, SegmentationSource, missing_ranges, parse_pieces, segmentation_prompt,
)
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.quality import QualityGate
from subtitle_llm.pipeline.task_store import TranslationTaskStore
from subtitle_llm.settings import PipelineConfig

PROTOCOL_VERSION = 2
MAX_REPAIR_CALLS = 2
MAX_ALIGNMENT_POSITIONS = 120
logger = logging.getLogger(__name__)


def model_request_key(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@dataclass
class GeneratedChunk:
    result: ChunkTranslationResult
    request_key: str
    pieces: list[Piece]
    accepted: bool = False
    removed_indices: list[int] = field(default_factory=list)


class ModelSegmenter:
    def __init__(
        self, source: SegmentationSource, translator: ChunkTranslator,
        options: PipelineConfig, context: str, target_language: str,
        task_store: TranslationTaskStore, task_id: str, report: TranslationReport,
    ):
        self.source = source
        self.translator = translator
        self.options = options
        self.context = context
        self.target_language = target_language
        self.task_store = task_store
        self.task_id = task_id
        self.report = report

    def translate(self, planned: PlannedChunk) -> GeneratedChunk:
        start, end = self.source.bounds(planned.entries)
        prompt = segmentation_prompt(self.source, start, end, self.context, self.target_language, self.options)
        key = self._request_key(planned, prompt, PROTOCOL_VERSION)
        saved = self.task_store.load_model_result(self.task_id, key)
        if saved is None:
            legacy_prompt = segmentation_prompt(
                self.source, start, end, self.context, self.target_language, self.options, legacy=True,
            )
            saved = self.task_store.load_model_result(self.task_id, self._request_key(planned, legacy_prompt, 1))
        pieces = [Piece(**item) for item in (saved or {}).get("pieces", [])]
        missing_ranges(pieces, start, end)  # 同时校验缓存中的范围。
        if saved and "accepted_entries" in saved:
            entries = [SubtitleEntry.from_dict(item) for item in saved["accepted_entries"]]
            return GeneratedChunk(self._result(entries, None), key, pieces, accepted=True,
                                  removed_indices=saved.get("removed_indices", []))

        trace_id = None
        if saved is None:
            pieces, trace_id = self._generate(planned, start, end, prompt)
            self._save_pieces(key, pieces)

        # 每次任务执行最多两次局部修复。仍有缺失时让片段失败，而非无限付费重试。
        repair_calls = 0
        for _ in range(MAX_REPAIR_CALLS):
            gaps = missing_ranges(pieces, start, end)
            if not gaps:
                break
            left, right = gaps[0]
            repair_prompt = segmentation_prompt(
                self.source, left, right, self.context, self.target_language, self.options, repair=True,
            )
            repair_calls += 1
            repaired, trace_id = self._generate(planned, left, right, repair_prompt, repair=True)
            pieces.extend(repaired)
            pieces.sort(key=lambda item: item.start)
            self._save_pieces(key, pieces)
            if not repaired:
                break

        gaps = missing_ranges(pieces, start, end)
        if gaps:
            raise ValueError(f"模型断句仍有未完成范围 {gaps}；已保存其他结果，恢复任务时仅修复这些范围")
        if repair_calls < MAX_REPAIR_CALLS and not (saved or {}).get("alignment_attempted"):
            pieces, alignment_trace = self._repair_alignment(planned, key, pieces)
            trace_id = alignment_trace or trace_id
        entries = self.source.to_cues(pieces, self.options)
        return GeneratedChunk(self._result(entries, trace_id), key, pieces)

    def _request_key(self, planned: PlannedChunk, prompt: str, version: int) -> str:
        # 关闭语义复核时保持既有缓存键；启用后不能复用未经此检查的接受结果。
        layout = self.options.model_dump(mode="json", exclude={"model_segmentation", "semantic_quality", "automatic_extra_ratio"})
        if self.options.semantic_quality != "off":
            layout["semantic_quality"] = self.options.semantic_quality
        return model_request_key({
            "version": version, "prompt": prompt,
            "source": [e.to_dict() for e in planned.entries],
            "model": self.translator.model_config.model_dump(mode="json"),
            "layout": layout,
        })

    def accept(self, generated: GeneratedChunk, entries: list[SubtitleEntry]) -> None:
        kept = {entry.index for entry in entries}
        self.task_store.save_model_result(self.task_id, generated.request_key, {
            "pieces": [piece.to_dict() for piece in generated.pieces],
            "accepted_entries": [entry.to_dict() for entry in entries],
            "removed_indices": [piece.start for piece in generated.pieces if piece.start not in kept],
        })

    def _save_pieces(self, key: str, pieces: list[Piece], *, alignment_attempted: bool = False) -> None:
        self.task_store.save_model_result(self.task_id, key, {
            "pieces": [piece.to_dict() for piece in pieces], "alignment_attempted": alignment_attempted,
        })

    def _alignment_candidates(self, pieces: list[Piece]) -> list[tuple[int, int]]:
        """沿用数字诊断筛选邻条；只找待判断范围，不用规则决定字幕切点。"""
        gate = QualityGate()
        ranges = []
        for i, piece in enumerate(pieces):
            source = self.source.source_text(piece.start, piece.end)
            if not gate.missing_number_tokens(source, piece.text):
                continue
            for j in (i - 1, i + 1):
                if not 0 <= j < len(pieces):
                    continue
                if gate.missing_number_tokens(source, piece.text + " " + pieces[j].text):
                    continue
                left, right = min(i, j), max(i, j)
                if pieces[right].end - pieces[left].start + 1 <= MAX_ALIGNMENT_POSITIONS:
                    ranges.append((left, right))
                    break
        return ranges

    def _repair_alignment(
        self, planned: PlannedChunk, key: str, pieces: list[Piece],
    ) -> tuple[list[Piece], str | None]:
        candidates = self._alignment_candidates(pieces)
        if not candidates:
            return pieces, None
        # 每片段最多一次可选对齐修正，且与结构补齐共用两次调用上限。
        left, right = candidates[0]
        start, end = pieces[left].start, pieces[right].end
        prompt = segmentation_prompt(
            self.source, start, end, self.context, self.target_language, self.options, repair=True,
        ) + (
            "\nA previous attempt moved a source number/name into a neighboring cue. "
            "Rebuild ONLY this range: choose boundaries and translations together so every number/name "
            "stays with its own source words. A single combined cue is allowed if readable.\n"
        )
        # 在请求前记录尝试，崩溃/网络失败后恢复也不重复付费做可选修正。
        self._save_pieces(key, pieces, alignment_attempted=True)
        try:
            repaired, trace_id = self._generate(planned, start, end, prompt, alignment=True)
        except Exception:
            logger.warning("局部对齐修正未完成，保留原结果交给质量检查/复核: chunk=%s", planned.index + 1, exc_info=True)
            return pieces, None
        candidate = pieces[:left] + repaired + pieces[right + 1:]
        if missing_ranges(repaired, start, end):
            return pieces, None
        gate = QualityGate()
        before = self.source.to_cues(pieces[left:right + 1], self.options)
        try:
            after = self.source.to_cues(repaired, self.options)
        except ValueError:
            return pieces, None
        before_types = {issue.issue_type for issue in gate.diagnose_chunk(before, target_language=self.target_language).issues}
        after_types = {issue.issue_type for issue in gate.diagnose_chunk(after, target_language=self.target_language).issues}
        if "number_mismatch" in after_types or after_types - before_types:
            return pieces, None
        self._save_pieces(key, candidate, alignment_attempted=True)
        return candidate, trace_id

    def _generate(
        self, planned: PlannedChunk, start: int, end: int, prompt: str, *, repair: bool = False,
        alignment: bool = False,
    ) -> tuple[list[Piece], str | None]:
        stage = "model-alignment-repair" if alignment else ("model-segmentation-repair" if repair else "model-segmentation")
        operation = self.translator.operations.create_completion(
            prompt, stage=stage, chunk=planned.entries, chunk_index=planned.index,
        )
        completion = operation.completion
        # 返回即计费：解析或局部修复失败也不能丢掉用量。
        self.report.token_usage.add_usage(completion.usage)
        # 每次请求（包括补齐）都从 1 编号；缓存、时间轴及人工合并仍使用稳定全片位置。
        offset = start - 1
        pieces = [Piece(piece.start + offset, piece.end + offset, piece.text)
                  for piece in parse_pieces(completion.content, 1, end - offset)]
        gaps = missing_ranges(pieces, start, end)
        trace_id = None
        recorder = self.translator.trace_recorder
        if recorder:
            trace_id = recorder.record_call(
                stage=stage, prompt=prompt, response=completion.content,
                model_config=self.translator.model_config, usage=completion.usage,
                finish_reason=completion.finish_reason,
                duration_ms=operation.duration_ms, chunk=planned.entries,
                chunk_index=planned.index, total_chunks=self.report.total_chunks,
                source_coverage=SourceCoverage(start, end, len(pieces), gaps),
            )
        if not pieces and (not completion.content.strip() or completion.finish_reason == "length"):
            raise ValueError("断句翻译没有可恢复输出；请检查模型输出额度，未自动重复完整请求")
        return pieces, trace_id

    @staticmethod
    def _result(entries: list[SubtitleEntry], trace_id: str | None) -> ChunkTranslationResult:
        return ChunkTranslationResult(
            entries, "\n".join(f"[{i}]\n{entry.translated_text}" for i, entry in enumerate(entries, 1)),
            CompletionUsage(), trace_id,
        )
