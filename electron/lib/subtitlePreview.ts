import type { SubtitlePreviewSnapshot } from "../types";

export function applySubtitlePreview(
  current: SubtitlePreviewSnapshot,
  value: unknown,
): SubtitlePreviewSnapshot {
  if (!value || typeof value !== "object") return current;
  const snapshot = value as SubtitlePreviewSnapshot;
  if (!Number.isSafeInteger(snapshot.revision) || snapshot.revision <= current.revision
    || typeof snapshot.final !== "boolean" || !Array.isArray(snapshot.entries)) return current;
  if (!snapshot.entries.every((entry) => entry && Number.isSafeInteger(entry.index)
    && Number.isFinite(entry.start) && Number.isFinite(entry.end) && entry.end >= entry.start
    && typeof entry.original_text === "string" && typeof entry.translated_text === "string"
    && typeof entry.needs_retranslation === "boolean")) return current;
  return { ...snapshot, entries: snapshot.entries.toSorted((a, b) => a.start - b.start || a.index - b.index) };
}
