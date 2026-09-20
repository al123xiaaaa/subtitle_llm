"""翻译首轮与持久化复核之间的接口。首轮收集证据，结束后统一分配额外额度。"""

from __future__ import annotations

import copy
import threading
from dataclasses import asdict

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.pipeline.quality import QualityGate
from .operations import WorkspaceOperations, mapped_issues
from .store import WorkspaceStore, content_hash


class RecordingQualityGate(QualityGate):
    def __init__(self, delegate):
        self.delegate = delegate
        self.records: dict[tuple[int, ...], dict] = {}
        self.cache = {}
        self.lock = threading.RLock()

    def diagnose_chunk(self, chunk, translation=None, target_language=None, *, planned=None):
        entries = [entry.to_dict() for entry in chunk]
        key = content_hash(entries)
        # 同一首轮证据的重复读取不增加模型开销；内容或上下文变化会得到新键。
        cache_key = (key, target_language, getattr(planned, "boundary_context", ""))
        with self.lock:
            cached = self.cache.get(cache_key)
        diagnosis = (
            copy.deepcopy(cached)
            if cached is not None and translation is None
            else self.delegate.diagnose_chunk(chunk, translation, target_language, planned=planned)
        )
        with self.lock:
            if translation is None:
                self.cache[cache_key] = copy.deepcopy(diagnosis)
            self.records[tuple(entry.index for entry in chunk)] = {
                "entries": entries,
                "diagnosis": copy.deepcopy(diagnosis),
                "boundary_context": getattr(planned, "boundary_context", ""),
            }
        return diagnosis

    def persist(self, workspace: WorkspaceStore, task_id: str, report, semantic_enabled: bool):
        doc = workspace.sync_task(task_id)
        current = {entry["index"]: entry for entry in doc["entries"]}
        for indices, record in self.records.items():
            if not set(indices).issubset(current):
                continue
            diagnosis = record["diagnosis"]
            issues = mapped_issues(record["entries"], diagnosis.issues)
            issues.extend(issue for issue in report.source_review_requests if set(issue["indices"]).issubset(indices))
            unavailable = any(issue["type"] == "semantic_unavailable" for issue in issues)
            details = {
                "diagnosis": asdict(diagnosis),
                "semantic_enabled": semantic_enabled,
                "evidence_entries": record["entries"],
                "boundary_context": record["boundary_context"],
            }
            jev = next(
                (check for check in reversed(report.semantic_quality_checks) if check["cue_ids"] == list(indices)), None
            )
            if jev:
                details["jev"] = jev
            status = "unavailable" if unavailable else "checked" if semantic_enabled else "not_checked"
            check = workspace.record_check(
                task_id,
                list(indices),
                [i for i in issues if i["type"] != "semantic_unavailable"],
                status=status,
                details=details,
            )
            if content_hash([current[i] for i in indices]) != content_hash(record["entries"]):
                workspace.expire_check(task_id, check["check_id"])
        workspace.protect(task_id, report.human_protected_indices, reason="TUI 人工确认")


def finish_first_pass(workspace, gate, task_id, subtitle, report, config, client):
    report.workspace_recorded = True
    current = {entry.index: entry.to_dict() for entry in subtitle.entries}
    for record in gate.records.values():
        if not all(entry["index"] in current for entry in record["entries"]) or content_hash(
            [current[entry["index"]] for entry in record["entries"]]
        ) != content_hash(record["entries"]):
            continue
        missing = [
            record["entries"][issue.index - 1]["index"]
            for issue in record["diagnosis"].issues
            if issue.issue_type in {"missing_translation", "placeholder_translation"}
            and 1 <= issue.index <= len(record["entries"])
        ]
        if missing:
            report.mark_failed(-1, missing, "该范围缺少有效译文")
    report.translation_complete = (
        bool(subtitle.entries) and not report.failed_chunks and all(e.translated_text.strip() for e in subtitle.entries)
    )
    report.first_pass_tokens += max(
        0, report.token_usage.total_tokens - report.summary_tokens - report.token_usage.estimated_tokens
    )
    report.summary_tokens_total += report.summary_tokens
    workspace.tasks.save_resume_state(task_id, subtitle, report)
    gate.persist(workspace, task_id, report, config.pipeline.semantic_quality == "jev")
    if config.pipeline.semantic_quality == "jev":
        operations = WorkspaceOperations(workspace, client=client)
        report.automatic_budget = operations.run_automatic(
            task_id, baseline=report.first_pass_tokens, ratio=config.pipeline.automatic_extra_ratio
        )
    operations = WorkspaceOperations(workspace, client=client)
    for request in report.manual_review_requests:
        check = workspace.record_check(
            task_id,
            request["context_indices"],
            [
                {
                    "indices": request["indices"],
                    "type": "manual_request",
                    "description": "TUI 请求追加处理；需单独额度，核验后才采用修复",
                }
            ],
            status="pending",
        )
        if request["token_limit"] > 0:
            try:
                operations.repair(task_id, check["items"][0]["item_id"], token_limit=request["token_limit"])
            except ValueError:
                pass  # 额度不足时保留明确待处理项，不解锁自动预算。
    doc = workspace.version(task_id)
    report.failed_chunks = (
        [type(report.failed_chunks[0])(**item) for item in doc["report"].get("failed_chunks", [])]
        if report.failed_chunks
        else []
    )
    report.translation_complete = doc["complete"]
    subtitle.entries = [SubtitleEntry.from_dict(entry) for entry in doc["entries"]]
