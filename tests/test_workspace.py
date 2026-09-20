import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.task_store import TranslationTaskStore
from subtitle_llm.settings import AppConfig, ModelConfig, ModelProvider
from subtitle_llm.workspace import WorkspaceConflict, WorkspaceStore


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tasks = TranslationTaskStore(self.root / "tasks.sqlite3")
        model = ModelConfig(
            type=ModelProvider.CUSTOM, api_key_env="FAKE_KEY", model="model-a", endpoint="https://fake.test"
        )
        self.config = AppConfig(summary_model=model, translation_model=model)
        self.workspace = WorkspaceStore(self.tasks)

    def task(self, url="https://www.youtube.com/watch?v=example", complete=True):
        record = self.tasks.create_task(
            input_display=url,
            working_directory=str(self.root),
            source_url=url,
            source_subtitle_path=str(self.root / "source.srt"),
            normalized_input_fingerprint="source",
            target_language="Chinese",
            source_language="en",
            output_format="source-first",
            output_file=str(self.root / "output.srt"),
            config=self.config,
        )
        entries = [
            SubtitleEntry(1, "00:00:01,000", "00:00:03,000", "Hello", "你好"),
            SubtitleEntry(2, "00:00:03,000", "00:00:05,000", "World", "世界" if complete else ""),
        ]
        report = TranslationReport(
            input_file="source.srt",
            output_file=record.output_file,
            context_file="",
            total_entries=2,
            accepted_entry_indices=[1, 2] if complete else [1],
            final_output_entries=2,
            target_language="Chinese",
        )
        self.tasks.save_resume_state(record.task_id, Subtitle(entries), report)
        self.tasks.update_status(record.task_id, "completed" if complete else "failed")
        return self.workspace.sync_task(record.task_id)

    def test_material_versions_survive_reopen_and_preserve_pinned_default(self):
        first = self.task()
        self.workspace.pin_version(first["material_id"], "Chinese", first["task_id"])
        second = self.task("https://youtu.be/example")
        self.task("https://youtu.be/example", complete=False)
        library = WorkspaceStore(self.tasks).list_materials()
        self.assertEqual(len(library), 1)
        material = self.workspace.material(first["material_id"])
        self.assertEqual(len(material["versions"]), 2)
        self.assertEqual(len(material["tasks"]), 3)
        self.assertEqual(material["defaults"]["Chinese"], first["task_id"])
        self.assertNotEqual(first["task_id"], second["task_id"])

    def test_edit_protects_text_and_invalidates_whole_chunk_evidence(self):
        doc = self.task()
        task_id = doc["task_id"]
        self.workspace.record_check(
            task_id,
            [1, 2],
            [
                {"indices": [1], "type": "semantic_meaning", "description": "语义疑点", "candidate_only": True},
            ],
            status="checked",
        )
        item = self.workspace.review_items(task_id)[0]
        self.workspace.accept_items(task_id, [item["item_id"]])
        doc = self.workspace.version(task_id)
        self.assertEqual(doc["checks"][0]["status"], "checked")
        self.assertEqual(self.workspace.review_items(task_id), [])
        self.assertIn(1, doc["protected_indices"])
        changed = self.workspace.edit(task_id, {2: {"translated_text": "这个世界"}}, expected_revision=0)
        self.assertEqual(changed["revision"], 1)
        self.assertTrue(changed["checks"][0]["outdated"])
        self.assertEqual(self.workspace.review_items(task_id)[0]["state"], "check_pending")
        self.assertEqual(WorkspaceStore(self.tasks).version(task_id)["entries"][1]["translated_text"], "这个世界")

    def test_undo_preserves_unrelated_edits_and_requires_explicit_conflict_choice(self):
        task_id = self.task()["task_id"]
        first = self.workspace.edit(task_id, {1: {"translated_text": "您好"}}, expected_revision=0)
        change_id = first["history"][-1]["id"]
        self.workspace.edit(task_id, {2: {"translated_text": "世间"}}, expected_revision=1)
        restored = self.workspace.undo(task_id, change_id, expected_revision=2)
        self.assertEqual([e["translated_text"] for e in restored["entries"]], ["你好", "世间"])
        self.workspace.edit(task_id, {1: {"translated_text": "大家好"}}, expected_revision=3)
        with self.assertRaises(WorkspaceConflict):
            self.workspace.undo(task_id, change_id, expected_revision=4)
        self.assertEqual(self.workspace.version(task_id)["entries"][0]["translated_text"], "大家好")

    def test_export_keeps_generation_snapshot_and_never_overwrites_old_files(self):
        task_id = self.task()["task_id"]
        pending = self.workspace.begin_artifact(task_id, "video")
        self.workspace.edit(task_id, {1: {"translated_text": "您好"}}, expected_revision=0)
        artifact = self.workspace.finish_artifact(task_id, pending["artifact_id"], path=str(self.root / "old.mkv"))
        self.assertTrue(artifact["outdated"])
        self.assertEqual(artifact["entries"][0]["translated_text"], "你好")
        exported = self.workspace.export_subtitle(task_id, self.root / "latest.srt")
        self.assertIn("您好", Path(exported["path"]).read_text())
        with self.assertRaises(FileExistsError):
            self.workspace.export_subtitle(task_id, self.root / "latest.srt")
        partial = self.task(complete=False)
        with self.assertRaises(ValueError):
            self.workspace.export_subtitle(partial["task_id"], self.root / "partial.srt")
        exported = self.workspace.export_subtitle(partial["task_id"], self.root / "partial.srt", partial=True)
        self.assertIn("未完成", Path(exported["path"]).name)
        self.assertEqual(exported["missing_indices"], [2])

    def test_repair_reads_whole_context_but_rejects_changes_outside_authorized_scope(self):
        from subtitle_llm.workspace.operations import WorkspaceOperations
        from subtitle_llm.llm.types import CompletionResult, CompletionUsage

        task_id = self.task()["task_id"]
        self.workspace.record_check(
            task_id,
            [1, 2],
            [
                {"indices": [1], "type": "semantic_meaning", "description": "语义疑点"},
            ],
            status="checked",
        )
        seen = []

        def evaluate(payload):
            seen.append(payload)
            return {
                "answers": {name: {"type": "boolean", "probability": 0.99} for name in payload["questions"]},
                "usage": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
            }

        class Client:
            def create_completion(self, config, messages):
                return CompletionResult(
                    '[{"index":1,"translated_text":"您好"},{"index":2,"translated_text":"被越界修改"}]',
                    CompletionUsage(100, 30, 130),
                )

        operations = WorkspaceOperations(self.workspace, evaluate=evaluate, client=Client())
        item = self.workspace.review_items(task_id)[0]
        result = operations.repair(task_id, item["item_id"], token_limit=50000)
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(self.workspace.version(task_id)["entries"][1]["translated_text"], "世界")
        self.assertEqual(len(seen[0]["state"]["context"]), 2)


class WorkspaceIntegrationTest(WorkspaceTest):
    def test_v3_upgrade_rolls_back_all_new_tables_on_interruption(self):
        import sqlite3
        from unittest.mock import patch
        from subtitle_llm.pipeline.workspace_storage import create_workspace_schema

        task_id = self.task()["task_id"]
        with sqlite3.connect(self.tasks.db_path) as connection:
            for name in [
                "workspace_budget_calls",
                "workspace_budgets",
                "workspace_sources",
                "workspace_versions",
                "workspace_materials",
            ]:
                connection.execute(f"DROP TABLE {name}")
            connection.execute("PRAGMA user_version=3")

        def interrupted(connection):
            create_workspace_schema(connection)
            raise RuntimeError("simulated v3 interruption")

        with (
            patch("subtitle_llm.pipeline.workspace_storage.create_workspace_schema", interrupted),
            self.assertRaises(RuntimeError),
        ):
            TranslationTaskStore(self.tasks.db_path)
        with sqlite3.connect(self.tasks.db_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertIsNone(
                connection.execute("SELECT name FROM sqlite_master WHERE name='workspace_versions'").fetchone()
            )
        self.assertEqual(TranslationTaskStore(self.tasks.db_path).get_task(task_id).task_id, task_id)

    def test_independent_source_is_visible_before_translation_and_preserves_its_snapshot(self):
        source = self.root / "downloaded.srt"
        source.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        material = self.workspace.import_source(str(source), language="en", source_url="https://youtu.be/example/")
        self.assertEqual(self.workspace.list_materials()[0]["material_id"], material["material_id"])
        self.assertEqual(material["versions"], [])
        source.write_text("changed later")
        self.assertIn("Hello", Path(material["sources"][0]["path"]).read_text())
        translated = self.task("https://www.youtube.com/watch?v=example")
        self.assertEqual(translated["material_id"], material["material_id"])

    def test_check_coverage_and_stale_response_after_resume(self):
        task_id = self.task()["task_id"]
        self.workspace.record_check(task_id, [1], [], status="checked")
        self.assertEqual(self.workspace.version(task_id)["check_state"], "incomplete")
        uncovered = self.workspace.review_items(task_id)
        self.assertEqual(uncovered[0]["indices"], [2])
        self.assertEqual([entry["index"] for entry in uncovered[0]["context"]], [1, 2])
        self.assertFalse(uncovered[0]["can_accept"])
        saved = self.tasks.load_resume_state(task_id)
        entries = [SubtitleEntry.from_dict(e) for e in saved["entries"].values()]
        entries[0].translated_text = "恢复后的新内容"
        self.tasks.save_resume_state(task_id, Subtitle(entries), TranslationReport(**saved["report"]))
        current = self.workspace.sync_task(task_id)
        self.assertEqual(current["revision"], 1)
        with self.assertRaises(WorkspaceConflict):
            self.workspace.record_check(task_id, [1, 2], [], status="checked", expected_revision=0)

    def test_text_and_timing_can_be_undone_separately_and_empty_translation_can_be_restored(self):
        task_id = self.task(complete=False)["task_id"]
        edited = self.workspace.edit(
            task_id, {2: {"translated_text": "世界", "end_time": "00:00:06,000"}}, expected_revision=0
        )
        self.assertEqual([h["category"] for h in edited["history"]], ["translation", "timing"])
        restored = self.workspace.undo(task_id, edited["history"][0]["id"], expected_revision=1)
        self.assertEqual(restored["entries"][1]["translated_text"], "")
        self.assertEqual(restored["entries"][1]["end_time"], "00:00:06,000")
        self.assertFalse(restored["complete"])
        restored = self.workspace.undo(task_id, edited["history"][1]["id"], expected_revision=2)
        self.assertEqual(restored["entries"][1]["end_time"], "00:00:05,000")

    def test_legacy_fallback_remains_unknown_after_timing_change_and_reload(self):
        task_id = self.task()["task_id"]
        saved = self.tasks.load_resume_state(task_id)
        entries = [SubtitleEntry.from_dict(e) for e in saved["entries"].values()]
        for entry in entries:
            entry.translated_text = entry.original_text
        self.tasks.save_resume_state(task_id, Subtitle(entries), TranslationReport(**saved["report"]))
        initial = self.workspace.sync_task(task_id)
        self.assertFalse(initial["complete"])
        changed = self.workspace.edit(
            task_id, {1: {"start_time": "00:00:00,500"}}, expected_revision=initial["revision"]
        )
        self.assertFalse(changed["complete"])
        self.assertFalse(self.workspace.sync_task(task_id)["complete"])

    def test_repair_acceptance_does_not_reactivate_unchecked_other_issues(self):
        from subtitle_llm.workspace.operations import WorkspaceOperations
        from subtitle_llm.llm.types import CompletionResult, CompletionUsage

        task_id = self.task()["task_id"]
        self.workspace.record_check(
            task_id,
            [1, 2],
            [
                {"indices": [1], "type": "semantic_meaning", "description": "疑点一"},
                {"indices": [2], "type": "semantic_addition", "description": "疑点二"},
            ],
            status="checked",
        )

        def evaluate(payload):
            return {
                "answers": {name: {"type": "boolean", "probability": 0.99} for name in payload["questions"]},
                "usage": {"totalTokens": 20},
            }

        class Client:
            def create_completion(self, config, messages):
                return CompletionResult('[{"index":1,"translated_text":"您好"}]', CompletionUsage(10, 10, 20))

        item = self.workspace.review_items(task_id)[0]
        result = WorkspaceOperations(self.workspace, evaluate=evaluate, client=Client()).repair(
            task_id, item["item_id"], token_limit=50000
        )
        self.assertEqual(result["status"], "applied")
        current = self.workspace.version(task_id)
        self.assertEqual(current["check_state"], "incomplete")
        self.assertTrue(current["checks"][0]["outdated"])
        self.assertFalse(
            any(c["status"] == "checked" and not c["outdated"] and 2 in c["indices"] for c in current["checks"])
        )

    def test_interrupted_migration_rolls_back_before_retry(self):
        import sqlite3
        from unittest.mock import patch

        task_id = self.task()["task_id"]
        with sqlite3.connect(self.tasks.db_path) as connection:
            connection.execute("DROP TABLE workspace_versions")
            connection.execute("DROP TABLE workspace_materials")
            connection.execute("PRAGMA user_version=1")
        create_schema = TranslationTaskStore._create_schema

        def interrupted(store, connection):
            create_schema(store, connection)
            raise RuntimeError("simulated interrupted migration")

        with patch.object(TranslationTaskStore, "_create_schema", interrupted), self.assertRaises(RuntimeError):
            TranslationTaskStore(self.tasks.db_path)
        with sqlite3.connect(self.tasks.db_path) as connection:
            self.assertEqual(connection.execute("SELECT task_id FROM translation_tasks").fetchone()[0], task_id)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
        recovered = TranslationTaskStore(self.tasks.db_path)
        self.assertEqual(recovered.get_task(task_id).task_id, task_id)

    def test_accepting_failed_content_does_not_make_it_complete_after_unrelated_edit(self):
        doc = self.task()
        task_id = doc["task_id"]
        saved = self.tasks.load_resume_state(task_id)
        report = TranslationReport(**saved["report"])
        report.mark_failed(0, [1], "缺少有效译文")
        report.translation_complete = False
        self.tasks.save_resume_state(
            task_id, Subtitle([SubtitleEntry.from_dict(e) for e in saved["entries"].values()]), report
        )
        self.workspace.sync_task(task_id)
        self.workspace.record_check(
            task_id,
            [1, 2],
            [{"indices": [1], "type": "missing_translation", "description": "未完成"}],
            status="checked",
        )
        self.workspace.accept_items(task_id, [self.workspace.review_items(task_id)[0]["item_id"]])
        edited = self.workspace.edit(task_id, {2: {"translated_text": "世间"}}, expected_revision=0)
        self.assertFalse(edited["complete"])
        partial = self.workspace.export_subtitle(task_id, self.root / "partial.srt", partial=True)
        self.assertEqual(partial["missing_indices"], [1])

    def test_resuming_after_edit_clears_failures_that_were_successfully_translated(self):
        doc = self.task(complete=False)
        task_id = doc["task_id"]
        saved = self.tasks.load_resume_state(task_id)
        report = TranslationReport(**saved["report"])
        report.mark_failed(0, [2], "调用失败")
        self.tasks.save_resume_state(
            task_id, Subtitle([SubtitleEntry.from_dict(e) for e in saved["entries"].values()]), report
        )
        self.workspace.sync_task(task_id)
        self.workspace.edit(task_id, {1: {"translated_text": "您好"}}, expected_revision=0)
        entries = [SubtitleEntry.from_dict(e) for e in saved["entries"].values()]
        entries[1].translated_text = "世界"
        report.failed_chunks = []
        report.translation_complete = True
        self.tasks.save_resume_state(task_id, Subtitle(entries), report)
        latest = self.workspace.sync_task(task_id)
        self.assertTrue(latest["complete"])
        self.assertEqual(latest["report"]["failed_chunks"], [])
        self.assertEqual(latest["entries"][0]["translated_text"], "您好")

    def test_system_source_evidence_derives_new_version_without_overwriting_parent(self):
        from unittest.mock import patch
        from subtitle_llm.workspace.media import retranscribe
        from subtitle_llm.workspace.operations import WorkspaceOperations
        from subtitle_llm.llm.types import CompletionResult, CompletionUsage

        doc = self.task()
        task_id = doc["task_id"]
        video = self.root / "source.mp4"
        video.touch()
        saved = self.tasks.load_resume_state(task_id)
        report = TranslationReport(**saved["report"])
        report.source_video_file = str(video)
        self.tasks.save_resume_state(
            task_id, Subtitle([SubtitleEntry.from_dict(e) for e in saved["entries"].values()]), report
        )
        self.workspace.sync_task(task_id)
        self.workspace.record_check(task_id, [1, 2], [], status="checked")
        seen = []

        def evaluate(payload):
            seen.append(payload)
            return {
                "answers": {name: {"type": "boolean", "probability": 0.99} for name in payload["questions"]},
                "usage": {"totalTokens": 25},
            }

        def recognize(audio, language, output, config):
            output.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello there\n")

        class Client:
            def create_completion(self, config, messages):
                return CompletionResult('[{"index":3,"translated_text":"大家好"}]', CompletionUsage(10, 10, 20))

        with (
            patch("subtitle_llm.workspace.media.subprocess.run"),
            patch("subtitle_llm.workspace.media.transcribe", recognize),
        ):
            result = retranscribe(
                WorkspaceOperations(self.workspace, evaluate=evaluate, client=Client()), task_id, [1], token_limit=50000
            )
        self.assertEqual(result["status"], "derived")
        derived = self.workspace.version(result["derived_task_id"])
        self.assertEqual(derived["entries"][0]["original_text"], "Hello there")
        self.assertEqual(derived["entries"][0]["translated_text"], "大家好")
        self.assertEqual(derived["entries"][1]["translated_text"], "世界")
        self.assertEqual(self.workspace.version(task_id)["entries"][0]["original_text"], "Hello")
        self.assertEqual(len(seen[0]["state"]["full_context"]), 2)
        self.assertEqual(derived["origin_operation"]["operation_id"], result["operation_id"])
        self.assertEqual(result["usage"], 70)
        self.assertEqual(result["budget"]["spent"], 70)
        self.assertEqual(derived["check_state"], "incomplete")

    def test_legacy_schema_migration_keeps_records_and_original_tables(self):
        import sqlite3

        doc = self.task()
        with sqlite3.connect(self.tasks.db_path) as connection:
            connection.execute("DROP TABLE workspace_versions")
            connection.execute("DROP TABLE workspace_materials")
            connection.execute("PRAGMA user_version=1")
        migrated = TranslationTaskStore(self.tasks.db_path)
        self.assertEqual(migrated.get_task(doc["task_id"]).task_id, doc["task_id"])
        self.assertEqual(len(migrated.load_resume_state(doc["task_id"])["entries"]), 2)
        self.assertTrue(self.tasks.db_path.with_suffix(".before-migration.sqlite3").exists())
        self.assertEqual(len(WorkspaceStore(migrated).tasks.list_tasks()), 1)

    def test_fork_preserves_parent_and_inherits_only_finished_cues(self):
        from subtitle_llm.workspace.versions import fork_version

        parent = self.task(complete=False)
        new = fork_version(self.workspace, parent["task_id"], model="model-b")
        self.assertNotEqual(new["task_id"], parent["task_id"])
        self.assertEqual(new["parent_task_id"], parent["task_id"])
        self.assertEqual(new["entries"][0]["translated_text"], "你好")
        self.assertEqual(new["entries"][1]["translated_text"], "")
        self.assertEqual(new["provenance"][0]["model"], "model-a")
        self.assertEqual(new["provenance"][1]["model"], "model-b")
        self.assertEqual(self.workspace.version(parent["task_id"])["provenance"][0]["model"], "model-a")

    def test_old_checkpoint_cannot_overwrite_human_edit_and_sync_keeps_operations(self):
        task_id = self.task()["task_id"]
        old = self.tasks.load_resume_state(task_id)
        self.workspace.edit(task_id, {1: {"translated_text": "人工确认译文"}}, expected_revision=0)
        self.workspace.save_operation(task_id, {"operation_id": "test", "status": "candidate"})
        self.tasks.save_resume_state(
            task_id,
            Subtitle([SubtitleEntry.from_dict(e) for e in old["entries"].values()]),
            TranslationReport(**old["report"]),
        )
        latest = self.workspace.sync_task(task_id)
        self.assertEqual(latest["entries"][0]["translated_text"], "人工确认译文")
        self.assertEqual(latest["operations"][0]["status"], "candidate")
        self.assertEqual(self.tasks.load_resume_state(task_id)["entries"]["1"]["translated_text"], "人工确认译文")

    def test_video_export_freezes_snapshot_while_editing_and_failure_keeps_subtitle(self):
        from subtitle_llm.workspace.media import export_video

        task_id = self.task()["task_id"]
        video = self.root / "source.mp4"
        video.touch()
        seen = []

        def mux(source, subtitle, output, **kwargs):
            seen.append(Path(subtitle).read_text())
            self.workspace.edit(task_id, {1: {"translated_text": "生成期间编辑"}}, expected_revision=0)
            Path(output).write_bytes(b"fake-video")

        result = export_video(self.workspace, task_id, str(self.root / "result.mkv"), video=str(video), mux=mux)
        self.assertIn("你好", seen[0])
        self.assertNotIn("生成期间编辑", seen[0])
        self.assertTrue(result["outdated"])
        self.assertEqual(self.workspace.version(task_id)["entries"][0]["translated_text"], "生成期间编辑")

    def test_recheck_preserves_same_human_decision_and_auto_cannot_bypass_it(self):
        task_id = self.task()["task_id"]
        issue = {"indices": [1], "type": "semantic_meaning", "description": "待核验"}
        self.workspace.record_check(task_id, [1, 2], [issue], status="checked")
        item = self.workspace.review_items(task_id)[0]
        self.workspace.accept_items(task_id, [item["item_id"]])
        self.workspace.record_check(task_id, [1, 2], [issue], status="checked")
        self.assertEqual(self.workspace.review_items(task_id), [])
        with self.assertRaises(WorkspaceConflict):
            self.workspace.edit(
                task_id, {1: {"translated_text": "不允许自动覆盖"}}, expected_revision=0, actor="automatic"
            )

    def test_failed_acceptance_retains_reservation_when_usage_is_unknown(self):
        from subtitle_llm.workspace.operations import WorkspaceOperations
        from subtitle_llm.llm.types import CompletionResult, CompletionUsage

        task_id = self.task()["task_id"]
        self.workspace.record_check(
            task_id, [1, 2], [{"indices": [1], "type": "semantic_meaning", "description": "候选"}], status="checked"
        )
        calls = []

        def evaluate(payload):
            calls.append(payload)
            if len(calls) == 2:
                raise RuntimeError("simulated outage")
            return {
                "answers": {name: {"type": "boolean", "probability": 0.99} for name in payload["questions"]},
                "usage": {"totalTokens": 20},
            }

        class Client:
            def create_completion(self, config, messages):
                return CompletionResult('[{"index":1,"translated_text":"您好"}]', CompletionUsage(10, 10, 20))

        operations = WorkspaceOperations(self.workspace, evaluate=evaluate, client=Client())
        item = self.workspace.review_items(task_id)[0]
        result = operations.repair(task_id, item["item_id"], token_limit=50000)
        self.assertEqual(result["status"], "candidate")
        self.assertIsNone(result["usage"])
        self.assertGreater(result["budget"]["reserved"], 0)
        self.assertEqual(self.workspace.version(task_id)["entries"][0]["translated_text"], "你好")
