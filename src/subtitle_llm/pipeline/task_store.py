from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.settings import AppConfig

SCHEMA_VERSION = 1


class TranslationTaskStoreError(RuntimeError):
    pass


class TranslationTaskNotFound(TranslationTaskStoreError):
    pass


class TranslationTaskMismatch(TranslationTaskStoreError):
    pass


@dataclass(frozen=True)
class TranslationTaskRecord:
    task_id: str
    status: str
    input_display: str
    working_directory: str
    source_subtitle_path: str
    normalized_input_fingerprint: str
    target_language: str
    source_language: str
    output_format: str
    output_file: str
    created_at: str
    updated_at: str
    config_snapshot_json: str
    context_file: str | None = None
    llm_trace_dir: str | None = None
    source_video_file: str | None = None
    source_url: str | None = None
    deleted_at: str | None = None


def default_task_db_path() -> Path:
    explicit = os.getenv("SUBTITLE_LLM_DB_PATH")
    if explicit:
        return Path(explicit).expanduser()

    user_data = os.getenv("SUBTITLE_LLM_USER_DATA_DIR")
    if user_data:
        return Path(user_data).expanduser() / "translation-tasks.sqlite3"

    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "subtitle-llm"
    elif sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "subtitle-llm"
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "subtitle-llm"
    return base / "translation-tasks.sqlite3"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def row_to_record(row: sqlite3.Row) -> TranslationTaskRecord:
    return TranslationTaskRecord(
        task_id=row["task_id"],
        status=row["status"],
        input_display=row["input_display"],
        working_directory=row["working_directory"],
        source_subtitle_path=row["source_subtitle_path"],
        normalized_input_fingerprint=row["normalized_input_fingerprint"],
        target_language=row["target_language"],
        source_language=row["source_language"],
        output_format=row["output_format"],
        output_file=row["output_file"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        config_snapshot_json=row["config_snapshot_json"],
        context_file=row["context_file"],
        llm_trace_dir=row["llm_trace_dir"],
        source_video_file=row["source_video_file"],
        source_url=row["source_url"],
        deleted_at=row["deleted_at"],
    )


class TranslationTaskStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path is not None else default_task_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise TranslationTaskStoreError(
                    f"任务记录数据库版本过新：{version} > {SCHEMA_VERSION}"
                )
            if version == 0:
                self._create_schema(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE translation_tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                input_display TEXT NOT NULL,
                working_directory TEXT NOT NULL,
                source_url TEXT,
                source_subtitle_path TEXT NOT NULL,
                normalized_input_fingerprint TEXT NOT NULL,
                target_language TEXT NOT NULL,
                source_language TEXT NOT NULL,
                output_format TEXT NOT NULL,
                output_file TEXT NOT NULL,
                context_file TEXT,
                llm_trace_dir TEXT,
                source_video_file TEXT,
                embedded_video_file TEXT,
                config_snapshot_json TEXT NOT NULL,
                report_json TEXT,
                error_summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            );

            CREATE INDEX idx_translation_tasks_resume
                ON translation_tasks (
                    normalized_input_fingerprint,
                    target_language,
                    output_format,
                    output_file,
                    updated_at
                )
                WHERE deleted_at IS NULL;

            CREATE TABLE translation_cues (
                task_id TEXT NOT NULL,
                cue_index INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                original_text TEXT NOT NULL,
                translated_text TEXT NOT NULL DEFAULT '',
                needs_retranslation INTEGER NOT NULL DEFAULT 0,
                removed INTEGER NOT NULL DEFAULT 0,
                chunk_index INTEGER,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (task_id, cue_index),
                FOREIGN KEY (task_id) REFERENCES translation_tasks(task_id) ON DELETE CASCADE
            );

            CREATE TABLE translation_chunks (
                task_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                status TEXT NOT NULL,
                entry_start INTEGER,
                entry_end INTEGER,
                entry_count INTEGER NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,
                diagnosis_json TEXT,
                last_trace_id TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (task_id, chunk_index),
                FOREIGN KEY (task_id) REFERENCES translation_tasks(task_id) ON DELETE CASCADE
            );
            """
        )

    def create_task(
        self,
        *,
        input_display: str,
        working_directory: str,
        source_subtitle_path: str,
        normalized_input_fingerprint: str,
        target_language: str,
        source_language: str,
        output_format: str,
        output_file: str,
        config: AppConfig,
        context_file: str | None = None,
        llm_trace_dir: str | None = None,
        source_video_file: str | None = None,
        source_url: str | None = None,
        task_id: str | None = None,
    ) -> TranslationTaskRecord:
        task_id = task_id or str(uuid.uuid4())
        timestamp = now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO translation_tasks (
                    task_id,
                    status,
                    input_display,
                    working_directory,
                    source_url,
                    source_subtitle_path,
                    normalized_input_fingerprint,
                    target_language,
                    source_language,
                    output_format,
                    output_file,
                    context_file,
                    llm_trace_dir,
                    source_video_file,
                    config_snapshot_json,
                    created_at,
                    updated_at
                )
                VALUES (?, 'created', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    input_display,
                    working_directory,
                    source_url,
                    source_subtitle_path,
                    normalized_input_fingerprint,
                    target_language,
                    source_language,
                    output_format,
                    output_file,
                    context_file,
                    llm_trace_dir,
                    source_video_file,
                    config_snapshot(config),
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str, *, include_deleted: bool = False) -> TranslationTaskRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM translation_tasks
                WHERE task_id = ?
                  AND (? OR deleted_at IS NULL)
                """,
                (task_id, int(include_deleted)),
            ).fetchone()
        if row is None:
            raise TranslationTaskNotFound(f"未找到翻译任务记录：{task_id}")
        return row_to_record(row)

    def find_resume_task(
        self,
        *,
        normalized_input_fingerprint: str,
        target_language: str,
        output_format: str,
        output_file: str,
    ) -> TranslationTaskRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM translation_tasks
                WHERE deleted_at IS NULL
                  AND normalized_input_fingerprint = ?
                  AND target_language = ?
                  AND output_format = ?
                  AND output_file = ?
                  AND status IN ('created', 'running', 'waiting_review', 'failed')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (normalized_input_fingerprint, target_language, output_format, output_file),
            ).fetchone()
        return row_to_record(row) if row else None

    def update_status(self, task_id: str, status: str, *, error_summary: str | None = None) -> None:
        timestamp = now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE translation_tasks
                SET status = ?,
                    error_summary = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (status, error_summary, timestamp, task_id),
            )

    def soft_delete(self, task_id: str) -> None:
        timestamp = now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE translation_tasks
                SET deleted_at = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (timestamp, timestamp, task_id),
            )

    def restore_deleted(self, task_id: str) -> None:
        timestamp = now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE translation_tasks
                SET deleted_at = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (timestamp, task_id),
            )

    def list_tasks(self, *, include_deleted: bool = False, limit: int = 100) -> list[TranslationTaskRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM translation_tasks
                WHERE (? OR deleted_at IS NULL)
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (int(include_deleted), limit),
            ).fetchall()
        return [row_to_record(row) for row in rows]

    def save_resume_state(self, task_id: str, subtitle: Subtitle, report: TranslationReport) -> None:
        timestamp = now_iso()
        report_json = report.model_dump_json()
        removed_indices = set(int(index) for index in report.removed_entry_indices)
        with self._connect() as connection:
            connection.execute("BEGIN")
            connection.execute(
                """
                UPDATE translation_tasks
                SET status = ?,
                    context_file = ?,
                    llm_trace_dir = ?,
                    source_video_file = ?,
                    embedded_video_file = ?,
                    report_json = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    task_status_for_report(report),
                    report.context_file,
                    report.llm_trace_dir,
                    report.source_video_file,
                    report.embedded_video_file,
                    report_json,
                    timestamp,
                    task_id,
                ),
            )
            for entry in subtitle.entries:
                connection.execute(
                    """
                    INSERT INTO translation_cues (
                        task_id,
                        cue_index,
                        start_time,
                        end_time,
                        original_text,
                        translated_text,
                        needs_retranslation,
                        removed,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id, cue_index) DO UPDATE SET
                        start_time = excluded.start_time,
                        end_time = excluded.end_time,
                        original_text = excluded.original_text,
                        translated_text = excluded.translated_text,
                        needs_retranslation = excluded.needs_retranslation,
                        removed = excluded.removed,
                        updated_at = excluded.updated_at
                    """,
                    (
                        task_id,
                        entry.index,
                        entry.start_time,
                        entry.end_time,
                        entry.original_text,
                        entry.translated_text,
                        int(entry.needs_retranslation),
                        int(entry.index in removed_indices),
                        timestamp,
                    ),
                )
            if removed_indices:
                connection.executemany(
                    """
                    UPDATE translation_cues
                    SET removed = 1, updated_at = ?
                    WHERE task_id = ? AND cue_index = ?
                    """,
                    [(timestamp, task_id, index) for index in removed_indices],
                )
            connection.execute("COMMIT")

    def save_chunk_state(
        self,
        task_id: str,
        *,
        chunk_index: int,
        entry_indices: list[int],
        status: str,
        diagnosis: dict[str, Any] | None = None,
        last_trace_id: str | None = None,
    ) -> None:
        timestamp = now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO translation_chunks (
                    task_id,
                    chunk_index,
                    status,
                    entry_start,
                    entry_end,
                    entry_count,
                    diagnosis_json,
                    last_trace_id,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, chunk_index) DO UPDATE SET
                    status = excluded.status,
                    entry_start = excluded.entry_start,
                    entry_end = excluded.entry_end,
                    entry_count = excluded.entry_count,
                    diagnosis_json = excluded.diagnosis_json,
                    last_trace_id = excluded.last_trace_id,
                    updated_at = excluded.updated_at
                """,
                (
                    task_id,
                    chunk_index,
                    status,
                    min(entry_indices) if entry_indices else None,
                    max(entry_indices) if entry_indices else None,
                    len(entry_indices),
                    json.dumps(diagnosis, ensure_ascii=False) if diagnosis else None,
                    last_trace_id,
                    timestamp,
                ),
            )

    def load_resume_state(self, task_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            task = connection.execute(
                "SELECT report_json FROM translation_tasks WHERE task_id = ? AND deleted_at IS NULL",
                (task_id,),
            ).fetchone()
            if task is None:
                raise TranslationTaskNotFound(f"未找到翻译任务记录：{task_id}")
            cue_rows = connection.execute(
                """
                SELECT *
                FROM translation_cues
                WHERE task_id = ?
                ORDER BY cue_index
                """,
                (task_id,),
            ).fetchall()

        entries = {
            str(row["cue_index"]): {
                "index": row["cue_index"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "original_text": row["original_text"],
                "translated_text": row["translated_text"],
                "needs_retranslation": bool(row["needs_retranslation"]),
            }
            for row in cue_rows
            if not row["removed"]
        }
        report_data = json.loads(task["report_json"] or "{}")
        return {"entries": entries, "report": report_data}

    def validate_task(
        self,
        record: TranslationTaskRecord,
        *,
        normalized_input_fingerprint: str,
        target_language: str,
        output_format: str,
        output_file: str,
    ) -> None:
        expected = {
            "normalized_input_fingerprint": normalized_input_fingerprint,
            "target_language": target_language,
            "output_format": output_format,
            "output_file": output_file,
        }
        actual = {
            "normalized_input_fingerprint": record.normalized_input_fingerprint,
            "target_language": record.target_language,
            "output_format": record.output_format,
            "output_file": record.output_file,
        }
        for key, value in expected.items():
            if actual[key] != value:
                raise TranslationTaskMismatch(
                    f"翻译任务记录不匹配：{key} expected={value!r}, actual={actual[key]!r}"
                )


def config_snapshot(config: AppConfig) -> str:
    return json.dumps(config.model_dump(mode="json", by_alias=True), ensure_ascii=False, sort_keys=True)


def config_from_snapshot(snapshot: str) -> AppConfig:
    return AppConfig.model_validate(json.loads(snapshot))


def task_status_for_report(report: TranslationReport) -> str:
    if report.stage == "完成":
        return "completed"
    if report.failed_chunks:
        return "failed"
    return "running"


def subtitle_from_resume_entries(entries: dict[str, Any]) -> Subtitle:
    return Subtitle([SubtitleEntry.from_dict(item) for item in entries.values()])
