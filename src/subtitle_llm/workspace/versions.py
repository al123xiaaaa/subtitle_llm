"""接续和源证据变化都创建独立任务，旧版本、原文和模型来源始终保留。"""

from __future__ import annotations

import copy
import uuid

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.io import SubtitleIO
from subtitle_llm.pipeline.checkpoint import file_fingerprint
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.task_store import config_from_snapshot


def fork_version(
    workspace,
    task_id: str,
    *,
    model: str | None = None,
    source_entries: list[dict] | None = None,
    rerun: bool = False,
    origin_operation: dict | None = None,
    verified_changes: dict[int, dict] | None = None,
) -> dict:
    old = workspace.version(task_id)
    record = workspace.tasks.get_task(task_id)
    config = config_from_snapshot(record.config_snapshot_json)
    if model:
        config.translation_model.model = model.strip()
        if not config.translation_model.model:
            raise ValueError("模型名称不能为空")
    config.pipeline.model_segmentation = "off"
    config.pipeline.normalize_subtitles = "off"
    config.pipeline.semantic_translation = "off"
    new_id = str(uuid.uuid4())
    directory = workspace.db_path.parent / "workspace-sources" / new_id
    directory.mkdir(parents=True, exist_ok=False)
    entries = copy.deepcopy(source_entries if source_entries is not None else old["entries"])
    originals = {entry["index"]: entry for entry in old["entries"]}
    failed = {i for chunk in old["report"].get("failed_chunks", []) for i in chunk["entry_indices"]}
    failed.update(old.get("unverified_indices", []))
    inherited = []
    for entry in entries:
        previous = originals.get(entry["index"])
        unchanged = previous and all(previous[key] == entry[key] for key in ("original_text", "start_time", "end_time"))
        if previous is not None and unchanged and not rerun and entry["index"] not in failed:
            entry["translated_text"] = previous["translated_text"]
            if entry["translated_text"].strip():
                inherited.append(entry["index"])
        else:
            entry["translated_text"] = ""
        if source_entries is not None and verified_changes and entry["index"] in verified_changes:
            entry.update(verified_changes[entry["index"]])
        entry["needs_retranslation"] = False
    # SRT 解析按出现次序编号，因此新版本明确重新映射，而非误用模型断句的位置 ID。
    index_map = {entry["index"]: position + 1 for position, entry in enumerate(entries)}
    inherited = [index_map[index] for index in inherited]
    for entry in entries:
        entry["index"] = index_map[entry["index"]]
    source = directory / "source.srt"
    SubtitleIO.write_srt(
        Subtitle([SubtitleEntry.from_dict(entry) for entry in entries]), source, output_format="source-only"
    )
    new = workspace.tasks.create_task(
        task_id=new_id,
        input_display=record.input_display,
        working_directory=record.working_directory,
        source_subtitle_path=str(source),
        normalized_input_fingerprint=file_fingerprint(source),
        target_language=record.target_language,
        source_language=record.source_language,
        output_format=record.output_format,
        output_file=str(directory / "translated.srt"),
        config=config,
        source_url=record.source_url,
        source_video_file=record.source_video_file,
        context_file=record.context_file,
    )
    report = TranslationReport(
        input_file=str(source),
        output_file=new.output_file,
        context_file=record.context_file or "",
        target_language=record.target_language,
        total_entries=len(entries),
        accepted_entry_indices=[entry["index"] for entry in entries if entry["translated_text"].strip()],
        workspace_recorded=True,
        source_video_file=record.source_video_file,
        translation_complete=bool(verified_changes) and all(entry["translated_text"].strip() for entry in entries),
    )
    workspace.tasks.save_resume_state(new_id, Subtitle([SubtitleEntry.from_dict(entry) for entry in entries]), report)
    workspace.sync_task(new_id, material_id=old["material_id"])
    provenance = []
    for origin in old["provenance"]:
        indices = [index_map[i] for i in origin["indices"] if i in index_map and index_map[i] in inherited]
        if indices:
            provenance.append(
                {
                    **origin,
                    "indices": indices,
                    "inherited_from_indices": [
                        i for i in origin["indices"] if i in index_map and index_map[i] in inherited
                    ],
                    "inherited": True,
                }
            )
    provenance.append(
        {
            "task_id": new_id,
            "indices": [e["index"] for e in entries if e["index"] not in inherited],
            "model": config.translation_model.model,
            "provider": config.translation_model.provider.value,
        }
    )
    return workspace.record_derivation(
        new_id,
        parent_task_id=task_id,
        provenance=provenance,
        protected_indices=[
            index_map[i] for i in old["protected_indices"] if i in index_map and index_map[i] in inherited
        ],
        inherited_indices=inherited,
        source_changed=source_entries is not None,
        origin_operation=origin_operation,
    )
