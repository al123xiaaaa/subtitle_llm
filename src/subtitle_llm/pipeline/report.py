from __future__ import annotations

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add_usage(self, usage) -> None:
        data = usage.to_dict() if hasattr(usage, "to_dict") else usage
        self.prompt_tokens += int(data.get("prompt_tokens", 0))
        self.completion_tokens += int(data.get("completion_tokens", 0))
        self.total_tokens += int(data.get("total_tokens", 0))


class FailedChunk(BaseModel):
    chunk_index: int
    entry_indices: list[int]
    error: str


class TranslationReport(BaseModel):
    input_file: str
    output_file: str
    checkpoint_file: str
    context_file: str
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
