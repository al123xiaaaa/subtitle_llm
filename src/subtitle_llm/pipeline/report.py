from __future__ import annotations

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # prompt 缓存命中情况（DeepSeek 等厂商支持）。命中部分计费更低。
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0

    def add_usage(self, usage) -> None:
        data = usage.to_dict() if hasattr(usage, "to_dict") else usage
        self.prompt_tokens += int(data.get("prompt_tokens", 0))
        self.completion_tokens += int(data.get("completion_tokens", 0))
        self.total_tokens += int(data.get("total_tokens", 0))
        self.prompt_cache_hit_tokens += int(data.get("prompt_cache_hit_tokens", 0))
        self.prompt_cache_miss_tokens += int(data.get("prompt_cache_miss_tokens", 0))

    def cache_hit_rate(self) -> int:
        """prompt 缓存命中率（百分比），缓存字段为 0 时返回 0。"""
        total = self.prompt_cache_hit_tokens + self.prompt_cache_miss_tokens
        return 100 * self.prompt_cache_hit_tokens // total if total else 0


class FailedChunk(BaseModel):
    chunk_index: int
    entry_indices: list[int]
    error: str


class AutoLayoutRepair(BaseModel):
    merged_index: int
    removed_index: int
    reason: str
    source: str = "semantic_layout"


class TranslationReport(BaseModel):
    input_file: str
    output_file: str
    context_file: str
    task_id: str | None = None
    task_db_file: str | None = None
    target_language: str = ""
    stage: str = "初始化"
    total_entries: int = 0
    processed_entries: int = 0
    short_entries: int = 0
    total_chunks: int = 0
    completed_chunks: int = 0
    resumed_entries: int = 0
    failed_chunks: list[FailedChunk] = Field(default_factory=list)
    boundary_risk_count: int = 0
    boundary_risks: list[dict] = Field(default_factory=list)
    normalized_source_file: str | None = None
    normalization_map_file: str | None = None
    normalization_applied: bool = False
    normalization_reason: str | None = None
    normalization_stats: dict = Field(default_factory=dict)
    semantic_translation_applied: bool = False
    semantic_units: int = 0
    semantic_multi_cue_units: int = 0
    removed_entry_indices: list[int] = Field(default_factory=list)
    final_output_entries: int = 0
    auto_layout_repairs: list[AutoLayoutRepair] = Field(default_factory=list)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    output_format: str = "source-first"
    llm_trace_dir: str | None = None
    source_video_file: str | None = None
    embedded_video_file: str | None = None
    embedded_video_error: str | None = None

    def mark_failed(self, chunk_index: int, entry_indices: list[int], error: Exception | str) -> None:
        self.failed_chunks.append(
            FailedChunk(
                chunk_index=chunk_index,
                entry_indices=entry_indices,
                error=str(error),
            )
        )
