from __future__ import annotations

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.review.ports import ReviewResult


class TuiReviewPort:
    """Textual review adapter.

    The current TUI implementation lives in the legacy module during migration.
    The translation pipeline only depends on this adapter and remains unaware of
    terminal spawning or temp-file IPC.
    """

    def __init__(self):
        from subtitle_llm.review.tui_manager import TUIManager

        self.manager = TUIManager()

    def review(self, chunk: list[SubtitleEntry], chunk_index: int, total_chunks: int) -> ReviewResult:
        data = self.manager.submit_chunk(
            [entry.to_dict() for entry in chunk],
            chunk_index=chunk_index,
            total_chunks=total_chunks,
        )
        if data is None:
            raise RuntimeError("TUI returned no data")

        selected_dicts = data.get("selected_subtitle_entries", [])
        merge_map = data.get("merge_map", [])
        updated_by_index = {entry.index: entry for entry in chunk}

        for merge_op in merge_map:
            merged_index = merge_op.get("merged_index")
            merged_from_indices = merge_op.get("merged_from_indices", [])
            if merged_index not in updated_by_index:
                continue
            if any(index not in updated_by_index for index in merged_from_indices):
                continue

            merged_entry = SubtitleEntry(
                index=merged_index,
                start_time=updated_by_index[merged_from_indices[0]].start_time,
                end_time=updated_by_index[merged_from_indices[-1]].end_time,
                original_text=" ".join(
                    updated_by_index[index].original_text for index in merged_from_indices
                ),
                translated_text=updated_by_index[merged_index].translated_text,
                needs_retranslation=updated_by_index[merged_index].needs_retranslation,
            )
            updated_by_index[merged_index] = merged_entry
            for index in merged_from_indices[1:]:
                updated_by_index.pop(index, None)

        for item in selected_dicts:
            entry = SubtitleEntry.from_dict(item)
            updated_by_index[entry.index] = entry

        updated_chunk = sorted(updated_by_index.values(), key=lambda item: item.index)
        return ReviewResult(
            chunk=updated_chunk,
            entries_to_retranslate=[
                SubtitleEntry.from_dict(item)
                for item in selected_dicts
                if item.get("needs_retranslation", False)
            ],
        )

    def stop(self) -> None:
        self.manager.stop()
