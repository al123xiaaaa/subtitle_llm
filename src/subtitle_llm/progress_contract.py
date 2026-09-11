from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.llm.types import CompletionUsage
from subtitle_llm.progress_events import ProgressEmitter, chunk_payload
from subtitle_llm.settings import ModelConfig

if TYPE_CHECKING:
    from subtitle_llm.pipeline.quality import ChunkDiagnosis


@dataclass(frozen=True)
class ProgressContract:
    emitter: ProgressEmitter

    def task_prepared(self) -> dict[str, Any]:
        return self.emitter.emit(
            stage="startup",
            detail="prepare_task",
            label="启动任务",
            message="正在准备翻译任务",
        )

    def local_input_selected(self, input_file: str) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_input",
            detail="local_input",
            label="准备输入",
            message=f"使用本地输入：{input_file}",
        )

    def url_input_detected(self) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_input",
            detail="url_input",
            label="准备输入",
            message="检测到视频 URL，准备获取字幕或音频",
        )

    def subtitle_ready(self, subtitle_path: str | Path) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_input",
            detail="subtitle_ready",
            status="done",
            label="字幕就绪",
            message=f"已获取字幕：{subtitle_path}",
        )

    def asr_ready(self, subtitle_path: str | Path) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_input",
            detail="asr_ready",
            status="done",
            label="ASR 完成",
            message=f"已生成字幕：{subtitle_path}",
        )

    def output_resolved(self, output_file: str) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_translation",
            detail="resolve_output",
            label="确定输出路径",
            message=f"字幕输出将写入 {output_file}",
        )

    def diagnostics_prepared(self, trace_dir: Path) -> dict[str, Any]:
        return self.emitter.emit(
            stage="startup",
            detail="prepare_diagnostics",
            label="准备诊断目录",
            message=f"LLM 诊断目录：{trace_dir}",
        )

    def subtitle_reading(self, input_file: str) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_input",
            detail="read_subtitle",
            label="读取字幕",
            message=f"正在读取字幕：{input_file}",
        )

    def subtitle_read(self, total_entries: int) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_input",
            detail="read_subtitle",
            status="done",
            label="读取字幕",
            message=f"已读取 {total_entries} 条字幕",
        )

    def normalization_started(self) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_input",
            detail="normalize_subtitle",
            label="规范化字幕",
            message="正在检查字幕是否需要重新断句和时间轴规范化",
        )

    def normalization_done(self, *, original_entries: int, normalized_entries: int) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_input",
            detail="normalize_subtitle",
            status="done",
            label="规范化字幕",
            message=f"已将 rolling caption 从 {original_entries} 条规范化为 {normalized_entries} 条",
        )

    def normalization_skipped(self, reason: str) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_input",
            detail="normalize_subtitle",
            status="skipped",
            label="规范化字幕",
            message=f"无需规范化字幕：{reason}",
        )

    def task_record_restored(self, resumed_entries: int) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_translation",
            detail="restore_task_record",
            status="done" if resumed_entries else "skipped",
            label="加载任务记录",
            message=f"从任务记录恢复 {resumed_entries} 条字幕" if resumed_entries else "没有可恢复任务记录，本次从头处理",
        )

    def context_generating(self, model: ModelConfig) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_translation",
            detail="generate_context",
            label="生成上下文",
            message="正在生成全局摘要和术语上下文",
            model=model,
        )

    def context_generated(
        self,
        *,
        context_file: str | Path,
        model: ModelConfig | dict[str, Any],
        usage: dict[str, Any] | CompletionUsage,
    ) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_translation",
            detail="generate_context",
            status="done",
            label="生成上下文",
            message=f"上下文已写入 {context_file}",
            model=model,
            usage=usage,
        )

    def semantic_units_planned(self, *, total_units: int, multi_cue_units: int) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_translation",
            detail="plan_semantic_units",
            status="done",
            label="构建语义单元",
            message=f"已构建 {total_units} 个语义单元，其中 {multi_cue_units} 个跨多条字幕",
        )

    def semantic_units_skipped(self) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_translation",
            detail="plan_semantic_units",
            status="skipped",
            label="构建语义单元",
            message="当前任务继续使用逐字幕片段翻译",
        )

    def chunks_planned(self, *, total_chunks: int, short_entries: int) -> dict[str, Any]:
        return self.emitter.emit(
            stage="prepare_translation",
            detail="plan_chunks",
            status="done",
            label="规划片段",
            message=f"已规划 {total_chunks} 个片段，短句保留 {short_entries} 条",
            total_chunks=total_chunks,
        )

    def chunk_pool_started(self, *, total_chunks: int, threads: int) -> dict[str, Any]:
        return self.emitter.emit(
            stage="processing_chunks",
            detail="start_chunk_pool",
            label="处理片段",
            message=f"开始并发处理 {total_chunks} 个片段，并发数 {threads}",
            total_chunks=total_chunks,
        )

    def chunk_queued(
        self,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
        semantic: bool = False,
    ) -> dict[str, Any]:
        label = "等待处理"
        return self.emitter.emit(
            stage="processing_chunks",
            detail="queue_chunk",
            label=label,
            message=f"{'语义片段' if semantic else '片段'} {chunk_index + 1}/{total_chunks} 已加入队列",
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status="waiting",
                detail="waiting",
            ),
            total_chunks=total_chunks,
        )

    def task_state_saved(
        self,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
        semantic: bool = False,
        warning: bool = False,
    ) -> dict[str, Any]:
        return self.emitter.emit(
            stage="processing_chunks",
            detail="save_task_state",
            status="done",
            label="保存进度",
            message=f"已保存{'语义片段' if semantic else '片段'} {chunk_index + 1}/{total_chunks} 的进度",
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status="warning" if warning else "done",
                detail="save_task_state",
            ),
            total_chunks=total_chunks,
        )

    def quality_checked(
        self,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
        diagnosis: ChunkDiagnosis,
        auto_repair: bool,
        stage_status: str = "running",
        chunk_status: str | None = None,
    ) -> dict[str, Any]:
        return self.emitter.emit(
            stage="processing_chunks",
            detail="quality",
            status=stage_status,
            label="质量检查",
            message=quality_message(chunk_index, total_chunks, diagnosis),
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status=chunk_status or quality_visual_status(diagnosis, auto_repair),
                detail="quality",
                issue_summary=diagnosis.summary if diagnosis.has_issues else "",
            ),
        )

    def chunk_accepted(
        self,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
        semantic: bool = False,
        warning: bool = False,
    ) -> dict[str, Any]:
        return self.emitter.emit(
            stage="processing_chunks",
            detail="accept_chunk",
            status="warning" if warning else "done",
            label="接受片段",
            message=(
                f"语义片段 {chunk_index + 1}/{total_chunks} 已接受并映射回字幕"
                if semantic
                else f"片段 {chunk_index + 1}/{total_chunks} 已接受"
            ),
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status="warning" if warning else "done",
                detail="accept_chunk",
            ),
        )

    def tui_wait(
        self,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
        semantic: bool = False,
    ) -> dict[str, Any]:
        return self.emitter.emit(
            stage="processing_chunks",
            detail="tui_wait",
            status="running",
            label="等待复核",
            message=f"{'语义片段' if semantic else '片段'} {chunk_index + 1}/{total_chunks} 等待 TUI 复核",
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status="review",
                detail="tui_wait",
            ),
        )

    def tui_accept(
        self,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
        semantic: bool = False,
    ) -> dict[str, Any]:
        return self.emitter.emit(
            stage="processing_chunks",
            detail="tui_accept",
            status="done",
            label="复核完成",
            message=f"{'语义片段' if semantic else '片段'} {chunk_index + 1}/{total_chunks} 已由用户接受",
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status="done",
                detail="tui_accept",
            ),
        )

    def tui_quality_passed(
        self,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
        semantic: bool = False,
    ) -> dict[str, Any]:
        return self.emitter.emit(
            stage="processing_chunks",
            detail="tui_quality",
            status="done",
            label="复核后质检",
            message=f"{'语义片段' if semantic else '片段'} {chunk_index + 1}/{total_chunks} 复核后通过质量检查",
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status="done",
                detail="tui_quality",
            ),
        )

    def tui_warning(
        self,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
        issue_summary: str,
        semantic: bool = False,
    ) -> dict[str, Any]:
        return self.emitter.emit(
            stage="processing_chunks",
            detail="tui_warning",
            status="warning",
            label="带风险继续",
            message=f"{'语义片段' if semantic else '片段'} {chunk_index + 1}/{total_chunks} 复核达到上限，带风险继续",
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status="warning",
                detail="tui_warning",
                issue_summary=issue_summary,
            ),
        )

    def ordinary_tui_retranslation_started(
        self,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
    ) -> dict[str, Any]:
        return self.emitter.emit(
            stage="processing_chunks",
            detail="tui_ordinary",
            label="TUI 普通重译",
            message=f"片段 {chunk_index + 1}/{total_chunks} 正在重译 {len(entries)} 行",
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status="repairing",
                detail="tui_ordinary",
            ),
        )

    def semantic_tui_retranslation_started(
        self,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
        semantic_unit_count: int,
    ) -> dict[str, Any]:
        return self.emitter.emit(
            stage="processing_chunks",
            detail="tui_semantic",
            label="TUI 语义修复重译",
            message=f"语义片段 {chunk_index + 1}/{total_chunks} 正在带 {semantic_unit_count} 个完整语义单元修复重译",
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status="repairing",
                detail="tui_semantic",
            ),
        )

    def alignment_drift_started(
        self,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
        drift_start: int,
        semantic: bool = False,
    ) -> dict[str, Any]:
        return self.emitter.emit(
            stage="processing_chunks",
            detail="drift",
            label="对齐漂移重译",
            message=f"{'语义片段' if semantic else '片段'} {chunk_index + 1}/{total_chunks} 从字幕 {drift_start} 开始重译",
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status="repairing",
                detail="drift",
            ),
        )

    def chunk_failed(
        self,
        entries: list[SubtitleEntry],
        *,
        chunk_index: int,
        total_chunks: int,
        error: Exception | str,
        semantic: bool = False,
    ) -> dict[str, Any]:
        return self.emitter.emit(
            stage="processing_chunks",
            detail="chunk_failed",
            status="failed",
            label="语义片段失败" if semantic else "片段失败",
            message=f"{'语义片段' if semantic else '片段'} {chunk_index + 1} 处理失败：{error}",
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status="failed",
                detail="chunk_failed",
                issue_summary=str(error),
            ),
        )

    def finalizing_subtitle(self) -> dict[str, Any]:
        return self.emitter.emit(
            stage="generate_result",
            detail="finalize_subtitle",
            label="汇总字幕",
            message="正在汇总所有字幕片段",
        )

    def writing_srt(self, output_file: str) -> dict[str, Any]:
        return self.emitter.emit(
            stage="generate_result",
            detail="write_srt",
            label="写出字幕",
            message=f"正在写出字幕：{output_file}",
        )

    def srt_written(self, output_file: str) -> dict[str, Any]:
        return self.emitter.emit(
            stage="generate_result",
            detail="write_srt",
            status="done",
            label="写出字幕",
            message=f"字幕已写出：{output_file}",
        )

    def complete(self, *, total_chunks: int) -> dict[str, Any]:
        return self.emitter.emit(
            stage="complete",
            detail="complete",
            status="done",
            label="完成",
            message="翻译任务完成",
            total_chunks=total_chunks,
        )

    def llm_chunk_event(
        self,
        *,
        detail: str,
        visual_status: str,
        message: str,
        entries: list[SubtitleEntry],
        chunk_index: int | None,
        total_chunks: int | None,
        model: ModelConfig | dict[str, Any],
        usage: CompletionUsage | None = None,
        duration_ms: int | None = None,
        trace_id: str | None = None,
        run_usage: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        normalized_detail = detail_key(detail)
        return self.emitter.emit(
            stage="processing_chunks",
            detail=normalized_detail,
            status="failed" if visual_status == "failed" else "running",
            label=chunk_stage_label(detail),
            message=message,
            chunk=chunk_payload(
                entries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                status=visual_status,
                detail=normalized_detail,
            ),
            model=model,
            usage=usage,
            trace_id=trace_id,
            duration_ms=duration_ms,
            run_usage=run_usage,
        )


def quality_message(chunk_index: int, total_chunks: int, diagnosis: ChunkDiagnosis) -> str:
    if diagnosis.has_issues:
        return (
            f"片段 {chunk_index + 1}/{total_chunks} 质量检查发现 "
            f"{diagnosis.flagged_entries} 行疑似问题：{diagnosis.summary}"
        )
    return f"片段 {chunk_index + 1}/{total_chunks} 质量检查通过"


def quality_visual_status(diagnosis: ChunkDiagnosis, auto_repair: bool) -> str:
    if not diagnosis.has_issues:
        return "done"
    if auto_repair:
        return "repairing"
    return "review"


def detail_key(stage: str) -> str:
    return stage.replace("-", "_")


def chunk_stage_label(stage: str) -> str:
    normalized = detail_key(stage)
    labels = {
        "rough": "初译",
        "refine": "润色",
        "semantic_rough": "语义初译",
        "semantic_cue_rough": "语义逐条初译",
        "semantic_refine": "语义润色",
        "semantic_repair": "语义自动修复",
        "source_correction_repair": "专名修复",
        "tui_semantic": "TUI 语义修复重译",
        "tui_semantic_repair": "TUI 语义修复重译",
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
