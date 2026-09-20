from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from subtitle_llm.pipeline.task_store import TranslationTaskStore


class WorkspaceConflict(ValueError):
    """当前内容已变化，调用方应刷新并让用户选择影响范围。"""


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(entries: list[dict]) -> str:
    content = [
        {key: entry.get(key) for key in ("index", "start_time", "end_time", "original_text", "translated_text")}
        for entry in entries
    ]
    return hashlib.sha256(encode(content).encode()).hexdigest()


def url_identity(source_url: str) -> str:
    """源字幕复用和素材归并共用同一 URL 身份规则。"""
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower()
    video_id = ""
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
        if not video_id and parsed.path.startswith(("/shorts/", "/embed/")):
            video_id = parsed.path.split("/")[2]
    elif host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/")
    if video_id:
        return f"youtube:{video_id}"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parsed.query) if not k.startswith("utm_")))
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", query, ""))


def source_identity(record) -> str:
    """优先可靠视频标识；本地字幕使用已保存的输入指纹，绝不按标题归并。"""
    if record.source_url:
        return url_identity(record.source_url)
    if record.normalized_input_fingerprint:
        return f"subtitle:{record.normalized_input_fingerprint}"
    return f"unknown:{record.task_id}"


class WorkspaceStore:
    """持久化版本内容和用户决定，桌面、CLI 与翻译管线共用此接口。"""

    def __init__(self, tasks: TranslationTaskStore):
        self.tasks = tasks
        self.db_path = tasks.db_path

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _read(connection, task_id: str) -> dict:
        row = connection.execute("SELECT document_json FROM workspace_versions WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("未找到该翻译版本或任务")
        return json.loads(row[0])

    @staticmethod
    def _write(connection, doc: dict) -> None:
        connection.execute(
            "UPDATE workspace_versions SET document_json=?, complete=?, updated_at=? WHERE task_id=?",
            (encode(doc), int(doc["complete"]), timestamp(), doc["task_id"]),
        )

    def sync_task(self, task_id: str, *, material_id: str | None = None) -> dict:
        record = self.tasks.get_task(task_id, include_deleted=True)
        state = self.tasks.load_resume_state(task_id)
        entries = sorted(state.get("entries", {}).values(), key=lambda entry: entry["index"])
        report = state.get("report", {})
        identity = source_identity(record)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT document_json FROM workspace_versions WHERE task_id=?", (task_id,)
            ).fetchone()
            if not previous and not material_id and not record.source_url:
                # 独立工具保存的是不可变源文件，按精确路径回到原素材，而不是猜测标题。
                source = connection.execute(
                    "SELECT material_id FROM workspace_sources WHERE json_extract(document_json,'$.path')=?",
                    (str(Path(record.input_display).expanduser().resolve()),),
                ).fetchone()
                if source:
                    material_id = source[0]
            if previous:
                # 手工历史一旦存在，任务恢复不能以旧快照覆盖当前版本。
                doc = json.loads(previous[0])
                material_id = doc["material_id"]
            if material_id:
                material = connection.execute(
                    "SELECT * FROM workspace_materials WHERE material_id=?", (material_id,)
                ).fetchone()
                if material is None:
                    raise ValueError("目标素材不存在")
            else:
                material = connection.execute(
                    "SELECT * FROM workspace_materials WHERE identity=?", (identity,)
                ).fetchone()
            if material is None:
                material_id = str(uuid.uuid4())
                title = Path(record.source_video_file or record.source_subtitle_path or record.input_display).stem
                connection.execute(
                    "INSERT INTO workspace_materials VALUES (?, ?, ?, ?, ?)",
                    (material_id, identity, title or "未命名素材", "{}", timestamp()),
                )
            else:
                material_id = material["material_id"]
            complete = bool(entries) and all(e.get("translated_text", "").strip() for e in entries)
            complete = (
                complete
                and not report.get("failed_chunks")
                and (
                    report.get("translation_complete") is True
                    or record.status in {"completed", "completed_with_warnings"}
                )
            )
            if report.get("translation_complete") is not None:
                complete = complete and bool(report["translation_complete"])
            if (
                not report.get("workspace_recorded")
                and entries
                and all(e.get("translated_text") == e.get("original_text") for e in entries)
            ):
                complete = False
            model = json.loads(record.config_snapshot_json).get("translation_model", {})
            doc = {
                "task_id": task_id,
                "material_id": material_id,
                "language": record.target_language,
                "source_language": record.source_language,
                "status": record.status,
                "complete": bool(complete),
                "revision": 0,
                "decision_revision": 0,
                "entries": entries,
                "checks": [],
                "history": [],
                "artifacts": [],
                "source": record.source_subtitle_path,
                "source_video": record.source_video_file,
                "source_url": record.source_url,
                "output_format": record.output_format,
                "context_file": record.context_file,
                "created_at": record.created_at,
                "provenance": [
                    {
                        "indices": [e["index"] for e in entries],
                        "task_id": task_id,
                        "model": model.get("model"),
                        "provider": model.get("type"),
                    }
                ],
                "legacy": not bool(report.get("workspace_recorded")),
                "report": report,
                "protected_indices": [],
                "parent_task_id": None,
                "unverified_indices": [e["index"] for e in entries]
                if not report.get("workspace_recorded")
                and entries
                and all(e.get("translated_text") == e.get("original_text") for e in entries)
                else [],
            }
            if not previous and doc["legacy"] and record.output_file and Path(record.output_file).is_file():
                doc["artifacts"].append(
                    {
                        "artifact_id": str(uuid.uuid4()),
                        "kind": "subtitle",
                        "revision": None,
                        "basis": None,
                        "entries": [],
                        "status": "ready",
                        "path": record.output_file,
                        "error": None,
                        "partial": not complete,
                        "missing_indices": [],
                        "created_at": record.created_at,
                        "outdated": True,
                        "basis_unknown": True,
                    }
                )
            if previous:
                old = json.loads(previous[0])
                # 同一任务的后续进度只能更新未编辑条目，历史和预算不能被检查点重置。
                edited = {i for h in old["history"] for i in h.get("indices", [])}
                current = {e["index"]: e for e in old["entries"]}
                doc["entries"] = [
                    current[e["index"]] if e["index"] in edited and e["index"] in current else e for e in entries
                ]
                for key in (
                    "revision",
                    "decision_revision",
                    "checks",
                    "artifacts",
                    "provenance",
                    "parent_task_id",
                    "history",
                    "protected_indices",
                    "operations",
                    "origin_operation",
                ):
                    if key in old:
                        doc[key] = old[key]
                if not report.get("workspace_recorded"):
                    doc["unverified_indices"] = sorted(
                        set(old.get("unverified_indices", [])) | set(doc["unverified_indices"])
                    )
                doc["complete"] = doc["complete"] and not doc["unverified_indices"]
                if content_hash(doc["entries"]) != content_hash(old["entries"]):
                    from .review import invalidate_checks

                    doc["revision"] += 1
                    invalidate_checks(doc)
                self._write(connection, doc)
            else:
                connection.execute(
                    "INSERT INTO workspace_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        task_id,
                        material_id,
                        record.target_language,
                        int(complete),
                        encode(doc),
                        timestamp(),
                        timestamp(),
                    ),
                )
        return self.version(task_id)

    def sync_all(self) -> None:
        for task in reversed(self.tasks.list_tasks(limit=100000)):
            self.sync_task(task.task_id)

    def version(self, task_id: str) -> dict:
        with self._connection() as connection:
            doc = self._read(connection, task_id)
        for artifact in doc["artifacts"]:
            artifact["outdated"] = artifact.get("basis") != content_hash(doc["entries"])
            artifact["missing"] = bool(artifact.get("path")) and not Path(artifact["path"]).is_file()
        current = [check for check in doc["checks"] if not check["outdated"]]
        covered = {i for check in current if check["status"] == "checked" for i in check["indices"]}
        required = {entry["index"] for entry in doc["entries"]}
        doc["check_state"] = (
            "checked"
            if required and required.issubset(covered) and all(check["status"] == "checked" for check in current)
            else "incomplete"
            if current
            else "unknown"
        )
        return doc

    def list_materials(self) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute("""SELECT m.* FROM workspace_materials m
                WHERE EXISTS (SELECT 1 FROM workspace_versions v JOIN translation_tasks t USING(task_id)
                    WHERE v.material_id=m.material_id AND t.deleted_at IS NULL)
                OR EXISTS (SELECT 1 FROM workspace_sources s WHERE s.material_id=m.material_id)
                ORDER BY m.created_at DESC""").fetchall()
        return [self.material(row["material_id"]) for row in rows]

    def material(self, material_id: str) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM workspace_materials WHERE material_id=?", (material_id,)).fetchone()
            if row is None:
                raise ValueError("未找到素材")
            versions = connection.execute(
                """SELECT v.* FROM workspace_versions v
                JOIN translation_tasks t USING(task_id) WHERE v.material_id=? AND t.deleted_at IS NULL
                ORDER BY v.created_at DESC, v.rowid DESC""",
                (material_id,),
            ).fetchall()
            sources = [
                json.loads(source[0])
                for source in connection.execute(
                    "SELECT document_json FROM workspace_sources WHERE material_id=? ORDER BY created_at DESC",
                    (material_id,),
                )
            ]
        summaries = []
        for version in versions:
            doc = json.loads(version["document_json"])
            summaries.append(
                {
                    key: doc[key]
                    for key in ("task_id", "language", "status", "complete", "revision", "legacy", "created_at")
                }
            )
        complete = [version for version in summaries if version["complete"]]
        pinned = json.loads(row["pinned_json"])
        defaults = {}
        for version in complete:
            defaults.setdefault(version["language"], version["task_id"])
        for language, task_id in pinned.items():
            if any(v["task_id"] == task_id for v in complete):
                defaults[language] = task_id
        return {
            "material_id": material_id,
            "title": row["title"],
            "identity": row["identity"],
            "sources": sources,
            "versions": complete,
            "tasks": summaries,
            "defaults": defaults,
            "pinned": pinned,
        }

    def pin_version(self, material_id: str, language: str, task_id: str | None) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT pinned_json FROM workspace_materials WHERE material_id=?", (material_id,)
            ).fetchone()
            if row is None:
                raise ValueError("未找到素材")
            pinned = json.loads(row[0])
            if task_id is not None:
                doc = self._read(connection, task_id)
                if doc["material_id"] != material_id or doc["language"] != language or not doc["complete"]:
                    raise ValueError("只能固定该素材、语言下的完整版本")
                pinned[language] = task_id
            else:
                pinned.pop(language, None)
            connection.execute(
                "UPDATE workspace_materials SET pinned_json=? WHERE material_id=?", (encode(pinned), material_id)
            )

    def record_check(
        self,
        task_id: str,
        indices: list[int],
        issues: list[dict],
        *,
        status: str,
        details: dict | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        from .review import make_check

        if status not in {"checked", "pending", "unavailable", "not_checked"}:
            raise ValueError("不支持的检查状态")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            doc = self._read(connection, task_id)
            if expected_revision is not None and doc["revision"] != expected_revision:
                raise WorkspaceConflict("检查期间字幕已修改，请基于最新内容重新检查")
            if not indices or not set(indices).issubset({e["index"] for e in doc["entries"]}):
                raise ValueError("检查范围不属于该版本")
            accepted = {
                (tuple(item["indices"]), tuple(sorted(issue["type"] for issue in item["issues"])))
                for old in doc["checks"]
                if old["revision"] == doc["revision"]
                for item in old["items"]
                if item["state"] == "accepted"
            }
            for old in doc["checks"]:
                if not old["outdated"] and old["indices"] == indices:
                    old["outdated"] = True
            check = make_check(doc, indices, issues, status, details or {})
            for item in check["items"]:
                if (tuple(item["indices"]), tuple(sorted(issue["type"] for issue in item["issues"]))) in accepted:
                    item["state"] = "accepted"
            doc["checks"].append(check)
            self._write(connection, doc)
            return check

    def review_items(self, task_id: str, *, include_all: bool = False) -> list[dict]:
        from .review import review_items
        from subtitle_llm.domain import SubtitleEntry
        from subtitle_llm.pipeline.chunks import ChunkPlanner
        from subtitle_llm.pipeline.task_store import config_from_snapshot

        doc = self.version(task_id)
        result = review_items(doc, include_all=include_all)
        covered = {i for check in doc["checks"] if not check["outdated"] for i in check["indices"]}
        missing = {entry["index"] for entry in doc["entries"]} - covered
        if missing:
            config = config_from_snapshot(self.tasks.get_task(task_id).config_snapshot_json).pipeline
            planned = ChunkPlanner(config.chunk_size, config.context_window_size, -1).plan(
                [SubtitleEntry.from_dict(entry) for entry in doc["entries"]]
            )
            for chunk in planned:
                indices = [entry.index for entry in chunk.entries]
                if missing.intersection(indices):
                    result.append(
                        {
                            "item_id": f"uncovered:{task_id}:{doc['revision']}:{chunk.index}",
                            "check_id": "",
                            "indices": sorted(missing.intersection(indices)),
                            "issues": [],
                            "state": "check_pending",
                            "outdated": False,
                            "context": [entry.to_dict() for entry in chunk.entries],
                            "check_status": "not_checked",
                            "can_accept": False,
                        }
                    )
        return result

    def accept_items(self, task_id: str, item_ids: list[str]) -> dict:
        if not item_ids:
            raise ValueError("请选择要保留的复核项")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            doc = self._read(connection, task_id)
            found = {}
            for check in doc["checks"]:
                if not check["outdated"]:
                    found.update({item["item_id"]: item for item in check["items"]})
            if not set(item_ids).issubset(found):
                raise WorkspaceConflict("所选复核项已过期，请刷新后选择；未完成检查不能标为通过")
            doc["decision_revision"] = doc.get("decision_revision", 0) + 1
            protected = set(doc["protected_indices"])
            for item_id in item_ids:
                found[item_id]["state"] = "accepted"
                protected.update(found[item_id]["indices"])
            doc["protected_indices"] = sorted(protected)
            doc["history"].append(
                {
                    "id": str(uuid.uuid4()),
                    "kind": "accept",
                    "at": timestamp(),
                    "revision": doc["revision"],
                    "item_ids": item_ids,
                    "indices": sorted({i for item_id in item_ids for i in found[item_id]["indices"]}),
                }
            )
            self._write(connection, doc)
            return doc

    def edit(
        self,
        task_id: str,
        changes: dict[int, dict],
        *,
        expected_revision: int,
        actor: str = "human",
        operation_id: str | None = None,
        expected_decision: int | None = None,
    ) -> dict:
        import copy
        from .review import invalidate_checks

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            doc = self._read(connection, task_id)
            if expected_decision is not None and doc.get("decision_revision", 0) != expected_decision:
                raise WorkspaceConflict("处理期间有人保留了当前内容，候选尚未覆盖人工决定")
            if doc["revision"] != expected_revision:
                raise WorkspaceConflict("字幕已更新，请刷新后再保存；本次输入尚未覆盖当前内容")
            changes = {int(key): value for key, value in changes.items()}
            entries = {e["index"]: e for e in doc["entries"]}
            if not changes or not set(changes).issubset(entries):
                raise ValueError("修改范围不属于当前字幕")
            if actor == "automatic" and set(changes) & set(doc["protected_indices"]):
                raise WorkspaceConflict("自动处理不能覆盖人工保护内容")
            before = [copy.deepcopy(entries[index]) for index in changes]
            for index, values in changes.items():
                if set(values) - {"translated_text", "start_time", "end_time"}:
                    raise ValueError("局部编辑不能改写原文或字幕身份")
                if "translated_text" in values and (
                    not isinstance(values["translated_text"], str)
                    or (not values["translated_text"].strip() and not (operation_id or "").startswith("undo:"))
                ):
                    raise ValueError("译文不能为空")
                entries[index].update(values)
            from .validation import validate_timing

            validate_timing(before, doc["entries"], set(changes))
            after = [copy.deepcopy(entries[index]) for index in changes]
            if before == after:
                return doc
            doc["revision"] += 1
            # 文本与时间使用独立历史，即使同一次保存同时修改，也能分别撤销。
            for category, fields in (("translation", ["translated_text"]), ("timing", ["start_time", "end_time"])):
                changed = {
                    left["index"]
                    for left, right in zip(before, after)
                    if any(left[field] != right[field] for field in fields)
                }
                if changed:
                    doc["history"].append(
                        {
                            "id": str(uuid.uuid4()),
                            "kind": "undo"
                            if (operation_id or "").startswith("undo:")
                            else ("edit" if actor == "human" else "repair"),
                            "category": category,
                            "fields": fields,
                            "unverified_before": doc.get("unverified_indices", []),
                            "failures_before": copy.deepcopy(doc["report"].get("failed_chunks", [])),
                            "at": timestamp(),
                            "revision": doc["revision"],
                            "actor": actor,
                            "operation_id": operation_id,
                            "before": [e for e in before if e["index"] in changed],
                            "after": [e for e in after if e["index"] in changed],
                            "indices": sorted(changed),
                        }
                    )
            if actor == "human":
                doc["protected_indices"] = sorted(set(doc["protected_indices"]) | set(changes))
            invalidate_checks(doc)
            repaired = {
                entry["index"]
                for entry in before
                if "translated_text" in changes[entry["index"]]
                and entry["translated_text"] != changes[entry["index"]]["translated_text"]
            }
            doc["unverified_indices"] = [i for i in doc.get("unverified_indices", []) if i not in repaired]
            for chunk in doc["report"].get("failed_chunks", []):
                chunk["entry_indices"] = [i for i in chunk["entry_indices"] if i not in repaired]
            doc["report"]["failed_chunks"] = [c for c in doc["report"].get("failed_chunks", []) if c["entry_indices"]]
            failed = {i for c in doc["report"].get("failed_chunks", []) for i in c.get("entry_indices", [])}
            if (operation_id or "").startswith("undo:"):
                undone = next(h for h in doc["history"] if h["id"] == (operation_id or "").removeprefix("undo:"))
                if "translated_text" in undone.get("fields", ["translated_text"]):
                    doc["unverified_indices"] = sorted(
                        set(doc["unverified_indices"]) | (set(undone.get("unverified_before", [])) & set(changes))
                    )
                    for chunk in undone.get("failures_before", []):
                        restored = {**chunk, "entry_indices": sorted(set(chunk["entry_indices"]) & set(changes))}
                        if restored["entry_indices"]:
                            doc["report"]["failed_chunks"].append(restored)
                    failed = {i for c in doc["report"]["failed_chunks"] for i in c["entry_indices"]}
            doc["complete"] = (
                bool(doc["entries"])
                and not failed
                and not doc.get("unverified_indices")
                and all(e["translated_text"].strip() for e in doc["entries"])
            )
            doc["report"]["translation_complete"] = doc["complete"]
            doc["report"]["accepted_entry_indices"] = sorted(
                (set(doc["report"].get("accepted_entry_indices", [])) | repaired)
                - {e["index"] for e in doc["entries"] if not e["translated_text"].strip()}
            )
            self._write(connection, doc)
            from subtitle_llm.pipeline.workspace_storage import write_current_content

            write_current_content(connection, doc, changes, timestamp())
            return doc

    def undo(self, task_id: str, history_id: str, *, expected_revision: int, force: bool = False) -> dict:
        doc = self.version(task_id)
        change = next((item for item in doc["history"] if item["id"] == history_id and item.get("before")), None)
        if change is None:
            raise ValueError("该记录没有可撤销的文本或时间调整")
        current = {entry["index"]: entry for entry in doc["entries"]}
        fields = change.get("fields", ["translated_text", "start_time", "end_time"])
        if not force and any(
            entry["index"] not in current or any(current[entry["index"]][key] != entry[key] for key in fields)
            for entry in change["after"]
        ):
            raise WorkspaceConflict("撤销范围存在后续修改；请选择保留当前内容或明确恢复该范围")
        changes = {entry["index"]: {key: entry[key] for key in fields} for entry in change["before"]}
        return self.edit(
            task_id, changes, expected_revision=expected_revision, actor="human", operation_id=f"undo:{history_id}"
        )

    def begin_artifact(self, task_id: str, kind: str, *, partial: bool = False) -> dict:
        import copy

        if kind not in {"subtitle", "video"}:
            raise ValueError("不支持的产物类型")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            doc = self._read(connection, task_id)
            if not doc["complete"] and not partial:
                raise ValueError("译文尚未完整，请明确选择导出已完成部分")
            failed = {
                int(index)
                for chunk in doc["report"].get("failed_chunks", [])
                for index in chunk.get("entry_indices", [])
            }
            failed.update(doc.get("unverified_indices", []))
            entries = [
                copy.deepcopy(e)
                for e in doc["entries"]
                if e.get("translated_text", "").strip() and e["index"] not in failed
            ]
            missing = [e["index"] for e in doc["entries"] if e["index"] not in {item["index"] for item in entries}]
            if not entries:
                raise ValueError("尚无可导出的已翻译内容")
            artifact = {
                "artifact_id": str(uuid.uuid4()),
                "kind": kind,
                "revision": doc["revision"],
                "basis": content_hash(doc["entries"]),
                "entries": entries,
                "status": "generating",
                "path": None,
                "error": None,
                "outdated": False,
                "partial": not doc["complete"],
                "missing_indices": missing,
                "created_at": timestamp(),
                "output_format": doc["output_format"],
            }
            doc["artifacts"].append(artifact)
            self._write(connection, doc)
            return artifact

    def finish_artifact(
        self, task_id: str, artifact_id: str, *, path: str | None = None, error: str | None = None
    ) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            doc = self._read(connection, task_id)
            artifact = next((a for a in doc["artifacts"] if a["artifact_id"] == artifact_id), None)
            if artifact is None:
                raise ValueError("未找到产物生成记录")
            artifact.update(
                status="failed" if error else "ready",
                path=path,
                error=error,
                outdated=artifact["basis"] != content_hash(doc["entries"]),
            )
            self._write(connection, doc)
            return artifact

    def export_subtitle(self, task_id: str, path: str | Path, *, partial: bool = False) -> dict:
        from .artifacts import write_snapshot

        artifact = self.begin_artifact(task_id, "subtitle", partial=partial)
        output = Path(path).expanduser().resolve()
        if artifact["partial"] and "未完成" not in output.stem:
            output = output.with_name(output.stem + ".未完成" + output.suffix)
        try:
            write_snapshot(artifact["entries"], output, artifact["output_format"])
        except Exception:
            self.finish_artifact(task_id, artifact["artifact_id"], error="字幕导出失败，目标可能已存在或不可写")
            raise
        return self.finish_artifact(task_id, artifact["artifact_id"], path=str(output))

    def save_operation(self, task_id: str, operation: dict) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            doc = self._read(connection, task_id)
            operations = doc.setdefault("operations", [])
            old = next((item for item in operations if item["operation_id"] == operation["operation_id"]), None)
            if old is not None:
                old.update(operation)
            else:
                if operation.get("revision") is not None and operation["revision"] != doc["revision"]:
                    raise WorkspaceConflict("追加操作开始前内容已更新，请重新确认范围")
                if operation.get("automatic") and any(
                    o.get("automatic") and set(o["indices"]) & set(operation["indices"]) for o in operations
                ):
                    raise WorkspaceConflict("该组疑点已有自动处理记录，不能并发开启第二轮")
                operations.append(operation)
            self._write(connection, doc)
        return operation

    def protect(self, task_id: str, indices: list[int], *, reason: str) -> None:
        if not indices:
            return
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            doc = self._read(connection, task_id)
            added = set(indices) - set(doc["protected_indices"])
            if not added:
                return
            doc["decision_revision"] = doc.get("decision_revision", 0) + 1
            doc["protected_indices"] = sorted(set(doc["protected_indices"]) | added)
            doc["history"].append(
                {
                    "id": str(uuid.uuid4()),
                    "kind": "accept",
                    "indices": sorted(added),
                    "at": timestamp(),
                    "revision": doc["revision"],
                    "reason": reason,
                }
            )
            for check in doc["checks"]:
                if not check["outdated"]:
                    for item in check["items"]:
                        if set(item["indices"]).issubset(added):
                            item["state"] = "accepted"
            self._write(connection, doc)

    def expire_check(self, task_id: str, check_id: str) -> None:
        from .review import make_check

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            doc = self._read(connection, task_id)
            check = next(c for c in doc["checks"] if c["check_id"] == check_id)
            check["outdated"] = True
            doc["checks"].append(make_check(doc, check["indices"], [], "pending", {}))
            self._write(connection, doc)

    def associate(self, task_id: str, material_id: str) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute(
                "SELECT 1 FROM workspace_materials WHERE material_id=?", (material_id,)
            ).fetchone():
                raise ValueError("目标素材不存在")
            doc = self._read(connection, task_id)
            doc["material_id"] = material_id
            connection.execute("UPDATE workspace_versions SET material_id=? WHERE task_id=?", (material_id, task_id))
            doc["history"].append(
                {"id": str(uuid.uuid4()), "kind": "associate", "at": timestamp(), "material_id": material_id}
            )
            self._write(connection, doc)
        return self.version(task_id)

    def record_derivation(
        self,
        task_id: str,
        *,
        parent_task_id: str,
        provenance: list[dict],
        protected_indices: list[int],
        inherited_indices: list[int],
        source_changed: bool,
        origin_operation: dict | None = None,
    ) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            doc = self._read(connection, task_id)
            doc.update(
                parent_task_id=parent_task_id,
                provenance=provenance,
                protected_indices=protected_indices,
                origin_operation=origin_operation,
            )
            doc["history"].append(
                {
                    "id": str(uuid.uuid4()),
                    "kind": "derive" if source_changed else "fork",
                    "at": timestamp(),
                    "parent_task_id": parent_task_id,
                    "inherited_indices": inherited_indices,
                }
            )
            self._write(connection, doc)
        return self.version(task_id)

    def import_source(
        self,
        path: str | None,
        *,
        language: str,
        source_url: str | None = None,
        media: str | None = None,
        material_id: str | None = None,
    ) -> dict:
        """独立下载和转写的结果也属于素材；字幕复制为不可变来源后再进入翻译。"""
        import shutil
        from subtitle_llm.pipeline.checkpoint import file_fingerprint

        original = Path(path).expanduser().resolve() if path else None
        media_file = Path(media).expanduser().resolve() if media else None
        if not ((original and original.is_file()) or (media_file and media_file.is_file())):
            raise ValueError("源字幕或媒体文件不存在")
        identity = (
            url_identity(source_url)
            if source_url
            else f"subtitle:{file_fingerprint(original)}"
            if original
            else f"media:{media_file}"
        )
        source_id = str(uuid.uuid4())
        snapshot = None
        if original:
            snapshot = self.db_path.parent / "workspace-sources" / source_id / original.name
            snapshot.parent.mkdir(parents=True, exist_ok=False)
            shutil.copy2(original, snapshot)
        doc = {
            "source_id": source_id,
            "path": str(snapshot) if snapshot else None,
            "language": language,
            "source_url": source_url,
            "source_video": str(media_file) if media_file and media_file.is_file() else None,
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            found = (
                connection.execute(
                    "SELECT material_id FROM workspace_materials WHERE material_id=?", (material_id,)
                ).fetchone()
                if material_id
                else connection.execute(
                    "SELECT material_id FROM workspace_materials WHERE identity=?", (identity,)
                ).fetchone()
            )
            if material_id and not found:
                raise ValueError("目标素材不存在")
            resolved_material_id = str(found[0]) if found else str(uuid.uuid4())
            if not found:
                anchor = original or media_file
                assert anchor is not None
                title = anchor.stem
                connection.execute(
                    "INSERT INTO workspace_materials VALUES (?, ?, ?, ?, ?)",
                    (resolved_material_id, identity, title, "{}", timestamp()),
                )
            connection.execute(
                "INSERT INTO workspace_sources VALUES (?, ?, ?, ?)", (source_id, resolved_material_id, encode(doc), timestamp())
            )
        return self.material(resolved_material_id)
