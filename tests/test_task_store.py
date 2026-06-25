import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.task_store import SCHEMA_VERSION, TranslationTaskStore
from subtitle_llm.settings import AppConfig, ModelConfig, ModelProvider, PipelineConfig


def make_config() -> AppConfig:
    model = ModelConfig(type=ModelProvider.CUSTOM, api_key_env="FAKE_KEY", model="fake", endpoint="https://fake.test")
    return AppConfig(
        summary_model=model,
        translation_model=model,
        pipeline=PipelineConfig(chunk_size=2, threads=1, context_window_size=1, review_mode="auto"),
    )


class TestTranslationTaskStore(unittest.TestCase):
    def test_initializes_schema_with_user_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "tasks.sqlite3"

            TranslationTaskStore(db_path)

            with sqlite3.connect(db_path) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                tables = {
                    row[0]
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertIn("translation_tasks", tables)
            self.assertIn("translation_cues", tables)
            self.assertIn("translation_chunks", tables)

    def test_saves_and_restores_resume_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TranslationTaskStore(Path(tmp) / "tasks.sqlite3")
            record = store.create_task(
                input_display="input.srt",
                working_directory=tmp,
                source_subtitle_path=str(Path(tmp) / "input.srt"),
                normalized_input_fingerprint="fingerprint",
                target_language="Chinese",
                source_language="en",
                output_format="source-first",
                output_file=str(Path(tmp) / "output.srt"),
                config=make_config(),
                context_file=str(Path(tmp) / "output_context.txt"),
                llm_trace_dir=str(Path(tmp) / "trace"),
            )
            subtitle = Subtitle([
                SubtitleEntry(1, "00:00:01,000", "00:00:02,000", "Hello", "你好"),
                SubtitleEntry(2, "00:00:03,000", "00:00:04,000", "World", ""),
            ])
            report = TranslationReport(
                input_file=str(Path(tmp) / "input.srt"),
                output_file=str(Path(tmp) / "output.srt"),
                context_file=str(Path(tmp) / "output_context.txt"),
                task_id=record.task_id,
                task_db_file=str(store.db_path),
                removed_entry_indices=[2],
            )

            store.save_resume_state(record.task_id, subtitle, report)
            store.save_chunk_state(
                record.task_id,
                chunk_index=0,
                entry_indices=[1, 2],
                status="done",
                diagnosis={"has_issues": False},
                last_trace_id="000001",
            )

            state = store.load_resume_state(record.task_id)
            self.assertEqual(state["entries"]["1"]["translated_text"], "你好")
            self.assertNotIn("2", state["entries"])
            self.assertEqual(state["report"]["removed_entry_indices"], [2])

            with sqlite3.connect(store.db_path) as connection:
                chunk = connection.execute(
                    "SELECT status, last_trace_id FROM translation_chunks WHERE task_id = ?",
                    (record.task_id,),
                ).fetchone()
            self.assertEqual(chunk, ("done", "000001"))

    def test_finds_resume_record_and_soft_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TranslationTaskStore(Path(tmp) / "tasks.sqlite3")
            record = store.create_task(
                input_display="input.srt",
                working_directory=tmp,
                source_subtitle_path=str(Path(tmp) / "input.srt"),
                normalized_input_fingerprint="fingerprint",
                target_language="Chinese",
                source_language="en",
                output_format="source-first",
                output_file=str(Path(tmp) / "output.srt"),
                config=make_config(),
            )
            store.update_status(record.task_id, "failed", error_summary="boom")

            found = store.find_resume_task(
                normalized_input_fingerprint="fingerprint",
                target_language="Chinese",
                output_format="source-first",
                output_file=str(Path(tmp) / "output.srt"),
            )
            self.assertIsNotNone(found)
            if found is None:
                self.fail("expected resumable task record")
            self.assertEqual(found.task_id, record.task_id)

            store.soft_delete(record.task_id)
            self.assertEqual(store.list_tasks(), [])
            self.assertEqual(store.list_tasks(include_deleted=True)[0].task_id, record.task_id)

            store.restore_deleted(record.task_id)
            self.assertEqual(store.list_tasks()[0].task_id, record.task_id)


if __name__ == "__main__":
    unittest.main()
