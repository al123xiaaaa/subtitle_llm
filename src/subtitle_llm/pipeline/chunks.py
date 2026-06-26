from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.pipeline.text import build_boundary_context, chunk_list

if TYPE_CHECKING:
    import tiktoken


@dataclass
class PlannedChunk:
    index: int
    entries: list[SubtitleEntry]
    boundary_context: str
    boundary_risks: list[dict]


class ChunkPlanner:
    def __init__(
        self,
        chunk_size: int,
        context_window_size: int,
        ignore_subtitle_length: int,
        *,
        max_output_tokens: int | None = None,
        encoder: tiktoken.Encoding | None = None,
        output_text_resolver: Callable[[SubtitleEntry], list[str]] | None = None,
    ):
        self.chunk_size = chunk_size
        self.context_window_size = context_window_size
        self.ignore_subtitle_length = ignore_subtitle_length
        self.max_output_tokens = max_output_tokens
        self.encoder = encoder
        self.output_text_resolver = output_text_resolver

    def plan(self, all_entries: list[SubtitleEntry], resumed_indices: set[int] | None = None) -> list[PlannedChunk]:
        resumed_indices = resumed_indices or set()
        chunks = chunk_list(
            all_entries,
            self.chunk_size,
            max_output_tokens=self.max_output_tokens,
            encoder=self.encoder,
            output_text_resolver=self.output_text_resolver,
        )
        planned: list[PlannedChunk] = []

        for chunk in chunks:
            filtered = [
                entry
                for entry in chunk
                if len(entry.original_text.strip()) > self.ignore_subtitle_length
                and entry.index not in resumed_indices
            ]
            if not filtered:
                continue
            context_data = build_boundary_context(all_entries, filtered, self.context_window_size)
            planned.append(
                PlannedChunk(
                    index=len(planned),
                    entries=filtered,
                    boundary_context=context_data["text"],
                    boundary_risks=context_data["risks"],
                )
            )

        return planned
