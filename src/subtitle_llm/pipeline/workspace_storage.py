"""任务与素材共享数据库的持久化边界；跨表写入始终复用调用方事务。"""

import json


def create_workspace_schema(connection):
    schema = """
                CREATE TABLE IF NOT EXISTS workspace_materials (
                    material_id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
                    pinned_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace_versions (
                    task_id TEXT PRIMARY KEY REFERENCES translation_tasks(task_id),
                    material_id TEXT NOT NULL REFERENCES workspace_materials(material_id),
                    language TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0,
                    document_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS workspace_versions_material ON workspace_versions(material_id, language);
                CREATE TABLE IF NOT EXISTS workspace_sources (
                    source_id TEXT PRIMARY KEY, material_id TEXT NOT NULL REFERENCES workspace_materials(material_id),
                    document_json TEXT NOT NULL, created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workspace_budgets (
                    bucket TEXT PRIMARY KEY, baseline INTEGER NOT NULL, ratio REAL NOT NULL, token_limit INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace_budget_calls (
                    call_id TEXT PRIMARY KEY, bucket TEXT NOT NULL REFERENCES workspace_budgets(bucket),
                    reserved INTEGER NOT NULL, actual INTEGER, state TEXT NOT NULL DEFAULT 'running'
                );
            """
    for statement in schema.split(";"):
        if statement.strip():
            connection.execute(statement)


def protected_entries(connection, task_id):
    row = connection.execute("SELECT document_json FROM workspace_versions WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        return {}
    doc = json.loads(row[0])
    return {e["index"]: e for e in doc["entries"] if e["index"] in doc["protected_indices"]}


def write_current_content(connection, doc, indices, timestamp):
    connection.execute(
        "UPDATE translation_tasks SET report_json=? WHERE task_id=?",
        (json.dumps(doc["report"], ensure_ascii=False), doc["task_id"]),
    )
    for entry in doc["entries"]:
        if entry["index"] in indices:
            connection.execute(
                "UPDATE translation_cues SET translated_text=?,start_time=?,end_time=?,updated_at=? "
                "WHERE task_id=? AND cue_index=?",
                (
                    entry["translated_text"],
                    entry["start_time"],
                    entry["end_time"],
                    timestamp,
                    doc["task_id"],
                    entry["index"],
                ),
            )
