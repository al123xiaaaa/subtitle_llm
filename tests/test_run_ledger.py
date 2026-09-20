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
from subtitle_llm.pipeline.run_ledger import RunLedger
from subtitle_llm.pipeline.task_store import TranslationTaskStore


class ResumeAcceptedEntryTest(unittest.TestCase):
    def test_acceptance_gate_survives_save_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TranslationTaskStore(Path(tmp) / "tasks.db")
            from subtitle_llm.settings import AppConfig, ModelConfig, ModelProvider
            model = ModelConfig(type=ModelProvider.CUSTOM, model="fake", endpoint="https://fake.test", api_key_env="FAKE_KEY")
            store.create_task(
                input_display="input.srt", working_directory=tmp,
                source_subtitle_path=str(Path(tmp) / "input.srt"),
                normalized_input_fingerprint="fp", target_language="Chinese",
                source_language="en", output_format="source-first",
                output_file=str(Path(tmp) / "output.srt"),
                config=AppConfig(summary_model=model, translation_model=model),
            )
            record = store.list_tasks()[0]
            task_id = record.task_id
            pending = SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "First", "暂存译文").set_needs_retranslation(True)
            accepted = SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "Second", "第二")
            report = TranslationReport(
                input_file="input.srt", output_file="output.srt", context_file="context.txt",
                task_id="t1", task_db_file=str(store.db_path),
            )
            ledger = RunLedger()
            subtitle = Subtitle([pending, accepted])
            ledger.save_task_state(store, task_id, subtitle, report, [pending, accepted])

            restored = RunLedger.restore_task_state(
                resume=True,
                task_id=task_id,
                subtitle=Subtitle([pending, accepted]),
                task_store=store,
                report=report,
            )

            # accepted_entry_indices 只包含明确接受条目，暂存译文必须重新翻译。
            self.assertEqual(restored.resumed_indices, {2})
            self.assertEqual(report.accepted_entry_indices, [2])
            self.assertEqual(restored.ledger.processed_entry_count([pending, accepted]), 1)



class RunLedgerTest(unittest.TestCase):
    def test_finalize_subtitle_keeps_missing_translation_explicit(self):
        subtitle = Subtitle([
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "First"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "Second"),
            SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "Third"),
        ])
        translated_entries = [
            SubtitleEntry(
                1,
                "00:00:00,000",
                "00:00:02,000",
                "First Second",
                "第一第二",
            )
        ]
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            context_file="context.txt",
        )
        ledger = RunLedger(removed_entry_indices={2})

        ledger.finalize_subtitle(subtitle, translated_entries, report)

        self.assertEqual([entry.index for entry in subtitle.entries], [1, 2])
        self.assertEqual(subtitle.entries[0].original_text, "First Second")
        self.assertEqual(subtitle.entries[0].translated_text, "第一第二")
        self.assertEqual(subtitle.entries[1].original_text, "Third")
        self.assertEqual(subtitle.entries[1].translated_text, "")
        self.assertTrue(subtitle.entries[1].needs_retranslation)
        self.assertEqual(report.failed_chunks[0].entry_indices, [3])
        self.assertEqual(report.stage, "完成")
        self.assertEqual(report.removed_entry_indices, [2])
        self.assertEqual(report.final_output_entries, 2)


if __name__ == "__main__":
    unittest.main()
