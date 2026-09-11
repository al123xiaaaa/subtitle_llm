import contextlib
import io
import json
import sqlite3
import sys
import os
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from typing import Any, cast

from subtitle_llm.llm.types import CompletionResult, CompletionUsage
from subtitle_llm.pipeline import TranslationRequest, TranslationService
from subtitle_llm.pipeline.checkpoint import file_fingerprint
from subtitle_llm.pipeline.chunk_translator import ChunkTranslator
from subtitle_llm.pipeline.quality import QualityGate
from subtitle_llm.pipeline.run_ledger import RunLedger
from subtitle_llm.pipeline.task_store import TranslationTaskMismatch, TranslationTaskStore
from subtitle_llm.progress_events import PROGRESS_EVENT_PREFIX
from subtitle_llm.review.tui import TuiReviewPort
from subtitle_llm.settings import AppConfig, ModelConfig, ModelProvider, PipelineConfig


class FakeLLMClient:
    def create_completion(self, config, messages):
        prompt = messages[-1]["content"]
        if "Analyze the following subtitle content" in prompt:
            return CompletionResult(
                content="总结: demo summary\n\n短语术语:\n- API(接口)",
                usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

        count = self._entry_count(prompt)
        body = "\n".join([f"[{i}]\n译文{i}" for i in range(1, count + 1)])
        if "<translation>" in prompt:
            body = f"<response><translation>\n{body}\n</translation></response>"
        return CompletionResult(
            content=body,
            usage=CompletionUsage(prompt_tokens=2, completion_tokens=2, total_tokens=4),
        )

    def _entry_count(self, prompt):
        import re

        for pattern in [r"\((\d+) entries\)", r"all (\d+) lines", r"exactly (\d+)"]:
            match = re.search(pattern, prompt)
            if match:
                return int(match.group(1))
        return 1


class RecordingLLMClient(FakeLLMClient):
    def __init__(self):
        self.prompts = []

    def create_completion(self, config, messages):
        self.prompts.append(messages[-1]["content"])
        return super().create_completion(config, messages)


class AlignmentDriftClient(RecordingLLMClient):
    def create_completion(self, config, messages):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if "Analyze the following subtitle content" in prompt:
            return CompletionResult(
                content="总结: demo summary\n\n短语术语:\n- API(接口)",
                usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
        if "alignment drift point" in prompt:
            return CompletionResult(
                content=(
                    "<response><translation>\n"
                    "[1]\n漂移修复一\n"
                    "[2]\n漂移修复二\n"
                    "</translation></response>"
                ),
                usage=CompletionUsage(prompt_tokens=3, completion_tokens=3, total_tokens=6),
            )
        if "refine a rough translation" in prompt:
            return CompletionResult(
                content="[1]\n稳定译文\n[2]\nTranslation missing line - 2\n[3]\nTranslation missing line - 3",
                usage=CompletionUsage(prompt_tokens=2, completion_tokens=2, total_tokens=4),
            )
        return super().create_completion(config, messages)


class InlineIndexClient(FakeLLMClient):
    def create_completion(self, config, messages):
        prompt = messages[-1]["content"]
        if "Analyze the following subtitle content" in prompt:
            return super().create_completion(config, messages)
        if "refine a rough translation" in prompt:
            count = self._entry_count(prompt)
            body = "\n".join([f"[{i}] 同行译文{i}" for i in range(1, count + 1)])
            return CompletionResult(
                content=body,
                usage=CompletionUsage(prompt_tokens=2, completion_tokens=2, total_tokens=4),
            )
        if "Previous flawed translation" in prompt:
            count = self._entry_count(prompt)
            body = "\n".join([f"[{i}]\n修复译文{i}" for i in range(1, count + 1)])
            return CompletionResult(
                content=f"<response><translation>\n{body}\n</translation></response>",
                usage=CompletionUsage(prompt_tokens=2, completion_tokens=2, total_tokens=4),
            )
        return super().create_completion(config, messages)


class TimeoutAfterBadTranslationClient(FakeLLMClient):
    def create_completion(self, config, messages):
        prompt = messages[-1]["content"]
        if "Analyze the following subtitle content" in prompt:
            return super().create_completion(config, messages)
        if "Previous flawed translation" in prompt:
            raise TimeoutError("local model timed out")
        return CompletionResult(
            content="[1]\n只翻译了第一句",
            usage=CompletionUsage(prompt_tokens=2, completion_tokens=2, total_tokens=4),
        )


class FailingTranslationClient(FakeLLMClient):
    def create_completion(self, config, messages):
        prompt = messages[-1]["content"]
        if "Analyze the following subtitle content" in prompt:
            return super().create_completion(config, messages)
        raise RuntimeError("chunk boom")


class StoppableReviewPort:
    def __init__(self):
        self.stopped = False

    def review(self, chunk, chunk_index, total_chunks, completed_chunks=0):
        raise AssertionError("review should not be called")

    def stop(self):
        self.stopped = True


class DriftReviewPort:
    def __init__(self):
        self.calls = 0
        self.stopped = False

    def review(self, chunk, chunk_index, total_chunks, completed_chunks=0):
        from subtitle_llm.review.ports import ReviewResult

        self.calls += 1
        return ReviewResult(
            chunk=chunk,
            entries_to_retranslate=[],
            alignment_drift_start_index=2,
        )

    def stop(self):
        self.stopped = True


class MergeAcceptReviewPort:
    def __init__(self):
        self.calls = 0
        self.stopped = False

    def review(self, chunk, chunk_index, total_chunks, completed_chunks=0):
        from subtitle_llm.domain import SubtitleEntry
        from subtitle_llm.review.ports import ReviewResult

        self.calls += 1
        merged_entry = SubtitleEntry(
            index=chunk[0].index,
            start_time=chunk[0].start_time,
            end_time=chunk[-1].end_time,
            original_text=" ".join(entry.original_text for entry in chunk),
            translated_text=" ".join(entry.translated_text for entry in chunk if entry.translated_text),
        )
        return ReviewResult(
            chunk=[merged_entry],
            entries_to_retranslate=[],
            removed_entry_indices=[entry.index for entry in chunk[1:]],
        )

    def stop(self):
        self.stopped = True


def make_config():
    model = ModelConfig(type=ModelProvider.CUSTOM, api_key_env="FAKE_KEY", model="fake", endpoint="https://fake.test")
    return AppConfig(
        summary_model=model,
        translation_model=model,
        pipeline=PipelineConfig(chunk_size=2, threads=1, context_window_size=1, review_mode="auto"),
    )


class TestNewPipeline(unittest.TestCase):
    def test_full_translation_with_fake_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHello world.\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nThis is a second line.\n\n",
                encoding="utf-8",
            )
            service = TranslationService(
                make_config(),
                translation_client=FakeLLMClient(),
                summary_client=FakeLLMClient(),
            )

            result = service.translate(
                TranslationRequest(
                    input_file=str(input_path),
                    output_file=str(output_path),
                    target_language="Chinese",
                )
            )

            output_text = output_path.read_text(encoding="utf-8")
            self.assertIn("Hello world.", output_text)
            self.assertIn("译文1", output_text)
            self.assertEqual(result.report.total_entries, 2)
            self.assertEqual(result.report.failed_chunks, [])
            self.assertTrue(Path(result.report.context_file).exists())
            self.assertTrue(result.report.task_id)
            self.assertTrue(result.report.task_db_file)
            self.assertTrue(Path(result.report.task_db_file or "").exists())
            self.assertFalse((output_path.parent / "output_checkpoint.json").exists())
            trace_dir = Path(result.report.llm_trace_dir or "")
            self.assertTrue(trace_dir.exists())
            trace_json_files = sorted(trace_dir.glob("*.json"))
            self.assertEqual(len(trace_json_files), 2)
            self.assertTrue(any("summary-context" in path.name for path in trace_json_files))
            self.assertTrue(any("chunk-001-rough-ok" in path.name for path in trace_json_files))
            self.assertFalse(any("chunk-001-refine-ok" in path.name for path in trace_json_files))
            with contextlib.closing(sqlite3.connect(result.report.task_db_file or "")) as connection:
                task_status = connection.execute(
                    "SELECT status FROM translation_tasks WHERE task_id = ?",
                    (result.report.task_id,),
                ).fetchone()[0]
                chunk_status = connection.execute(
                    "SELECT status FROM translation_chunks WHERE task_id = ? AND chunk_index = 0",
                    (result.report.task_id,),
                ).fetchone()[0]
            self.assertEqual(task_status, "completed")
            self.assertEqual(chunk_status, "accepted")

    def test_refine_translation_can_be_enabled_per_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHello world.\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nThis is a second line.\n\n",
                encoding="utf-8",
            )
            service = TranslationService(
                make_config(),
                translation_client=FakeLLMClient(),
                summary_client=FakeLLMClient(),
            )

            result = service.translate(
                TranslationRequest(
                    input_file=str(input_path),
                    output_file=str(output_path),
                    target_language="Chinese",
                    refine_translation=True,
                )
            )

            trace_dir = Path(result.report.llm_trace_dir or "")
            trace_json_files = sorted(trace_dir.glob("*.json"))
            self.assertEqual(len(trace_json_files), 3)
            self.assertTrue(any("chunk-001-refine-ok" in path.name for path in trace_json_files))

    def test_translation_normalizes_rolling_caption_before_chunking(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "demo.en.srt"
            output_path = Path(tmp) / "demo.zh.srt"
            input_path.write_text(
                "1\n00:00:00,000 --> 00:00:04,760\nA few months ago, I wrote a few\n\n"
                "2\n00:00:02,240 --> 00:00:06,120\nsentences, about four sentences, that\n\n"
                "3\n00:00:04,760 --> 00:00:09,040\nhave turned out to be the most\n\n"
                "4\n00:00:06,120 --> 00:00:10,760\ninfluential four sentences I've ever\n\n"
                "5\n00:00:09,040 --> 00:00:13,120\nwritten. I packaged these four sentences\n\n"
                "6\n00:00:10,760 --> 00:00:15,800\nup into the Grill Me skill, which is a\n\n"
                "7\n00:00:13,120 --> 00:00:17,560\nskill that you can use to get the LLM to\n\n"
                "8\n00:00:15,800 --> 00:00:19,560\ninterview you relentlessly.\n\n",
                encoding="utf-8",
            )
            service = TranslationService(
                AppConfig(
                    summary_model=make_config().summary_model,
                    translation_model=make_config().translation_model,
                    pipeline=PipelineConfig(
                        chunk_size=10,
                        threads=1,
                        context_window_size=1,
                        review_mode="auto",
                        normalize_max_cue_chars=220,
                        normalize_max_duration=30,
                    ),
                ),
                translation_client=FakeLLMClient(),
                summary_client=FakeLLMClient(),
            )

            result = service.translate(
                TranslationRequest(
                    input_file=str(input_path),
                    output_file=str(output_path),
                    target_language="Chinese",
                    source_language="en",
                )
            )

            self.assertTrue(result.report.normalization_applied)
            self.assertEqual(result.report.total_entries, 2)
            self.assertTrue(Path(result.report.normalized_source_file or "").exists())
            self.assertTrue(Path(result.report.normalization_map_file or "").exists())
            normalized_text = Path(result.report.normalized_source_file or "").read_text(encoding="utf-8")
            self.assertIn("A few months ago", normalized_text)
            self.assertIn("interview you relentlessly.", " ".join(normalized_text.split()))
            output_text = output_path.read_text(encoding="utf-8")
            self.assertIn("译文1", output_text)
            self.assertIn("译文2", output_text)

    def test_llm_trace_marks_inline_index_response_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHello world.\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nThis is a second line.\n\n",
                encoding="utf-8",
            )
            service = TranslationService(
                AppConfig(
                    summary_model=make_config().summary_model,
                    translation_model=make_config().translation_model,
                    pipeline=PipelineConfig(
                        chunk_size=2,
                        threads=1,
                        context_window_size=1,
                        review_mode="auto",
                        refine_translation=True,
                    ),
                ),
                translation_client=InlineIndexClient(),
                summary_client=FakeLLMClient(),
            )

            result = service.translate(
                TranslationRequest(
                    input_file=str(input_path),
                    output_file=str(output_path),
                    target_language="Chinese",
                )
            )

            import json

            trace_dir = Path(result.report.llm_trace_dir or "")
            refine_trace = next(path for path in trace_dir.glob("*chunk-001-refine-failed.json"))
            data = json.loads(refine_trace.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "failed")
            self.assertEqual(data["parse"]["parsed_count"], 0)
            self.assertEqual(data["parse"]["placeholder_count"], 2)
            self.assertEqual(data["response_shape"]["inline_index_markers"], 2)
            response_path = trace_dir / data["files"]["response"]
            self.assertIn("[1] 同行译文1", response_path.read_text(encoding="utf-8"))

    def test_translation_emits_user_visible_work_progress_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHello world.\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nThis is a second line.\n\n",
                encoding="utf-8",
            )
            service = TranslationService(
                make_config(),
                translation_client=FakeLLMClient(),
                summary_client=FakeLLMClient(),
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                service.translate(
                    TranslationRequest(
                        input_file=str(input_path),
                        output_file=str(output_path),
                        target_language="Chinese",
                    )
                )

            events = [
                json.loads(line.removeprefix(PROGRESS_EVENT_PREFIX))
                for line in stdout.getvalue().splitlines()
                if line.startswith(PROGRESS_EVENT_PREFIX)
            ]
            details = {event["detail"] for event in events}
            self.assertIn("prepare_task", details)
            self.assertIn("generate_context", details)
            self.assertIn("plan_chunks", details)
            self.assertIn("rough", details)
            self.assertNotIn("refine", details)
            self.assertIn("quality", details)
            self.assertIn("write_srt", details)
            self.assertIn("complete", details)
            self.assertTrue(
                any(
                    event.get("chunk", {}).get("status") == "done"
                    for event in events
                    if event["detail"] in {"quality", "accept_chunk", "save_task_state"}
                )
            )

    def test_translate_resume_restores_from_task_record(self):
        from subtitle_llm.domain import Subtitle, SubtitleEntry
        from subtitle_llm.pipeline.report import TranslationReport

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHello world.\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nThis is a second line.\n\n",
                encoding="utf-8",
            )
            task_store = TranslationTaskStore(Path(tmp) / "tasks.sqlite3")
            record = task_store.create_task(
                input_display=str(input_path),
                working_directory=tmp,
                source_subtitle_path=str(input_path),
                normalized_input_fingerprint=file_fingerprint(input_path),
                target_language="Chinese",
                source_language="en",
                output_format="source-first",
                output_file=str(output_path),
                config=make_config(),
            )
            partial_subtitle = Subtitle([
                SubtitleEntry(1, "00:00:01,000", "00:00:02,000", "Hello world.", "旧译文一"),
                SubtitleEntry(2, "00:00:03,000", "00:00:04,000", "This is a second line."),
            ])
            partial_report = TranslationReport(
                input_file=str(input_path),
                output_file=str(output_path),
                context_file=str(Path(tmp) / "context.txt"),
                task_id=record.task_id,
                task_db_file=str(task_store.db_path),
            )
            RunLedger().save_task_state(task_store, record.task_id, partial_subtitle, partial_report, [partial_subtitle.entries[0]])

            service = TranslationService(
                make_config(),
                translation_client=FakeLLMClient(),
                summary_client=FakeLLMClient(),
                task_store=task_store,
            )

            result = service.translate(
                TranslationRequest(
                    input_file=str(input_path),
                    output_file=str(output_path),
                    target_language="Chinese",
                    resume=True,
                )
            )

            output_text = output_path.read_text(encoding="utf-8")
            self.assertEqual(result.report.resumed_entries, 1)
            self.assertIn("旧译文一", output_text)
            self.assertIn("译文1", output_text)

    def test_translate_task_id_resume_uses_stored_paths(self):
        from subtitle_llm.domain import Subtitle, SubtitleEntry
        from subtitle_llm.pipeline.report import TranslationReport

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHello world.\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nThis is a second line.\n\n",
                encoding="utf-8",
            )
            task_store = TranslationTaskStore(Path(tmp) / "tasks.sqlite3")
            record = task_store.create_task(
                input_display=str(input_path),
                working_directory=tmp,
                source_subtitle_path=str(input_path),
                normalized_input_fingerprint=file_fingerprint(input_path),
                target_language="Chinese",
                source_language="en",
                output_format="source-first",
                output_file=str(output_path),
                config=make_config(),
            )
            partial_subtitle = Subtitle([
                SubtitleEntry(1, "00:00:01,000", "00:00:02,000", "Hello world.", "旧译文一"),
                SubtitleEntry(2, "00:00:03,000", "00:00:04,000", "This is a second line."),
            ])
            partial_report = TranslationReport(
                input_file=str(input_path),
                output_file=str(output_path),
                context_file=str(Path(tmp) / "context.txt"),
                task_id=record.task_id,
                task_db_file=str(task_store.db_path),
            )
            RunLedger().save_task_state(task_store, record.task_id, partial_subtitle, partial_report, [partial_subtitle.entries[0]])

            service = TranslationService(
                make_config(),
                translation_client=FakeLLMClient(),
                summary_client=FakeLLMClient(),
                task_store=task_store,
            )

            result = service.translate(
                TranslationRequest(
                    input_file=None,
                    output_file=None,
                    target_language="",
                    resume=True,
                    task_id=record.task_id,
                )
            )

            self.assertEqual(result.report.task_id, record.task_id)
            self.assertEqual(result.report.resumed_entries, 1)
            self.assertTrue(output_path.exists())

    def test_failed_auto_repair_replaces_partial_placeholders_with_source_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHello there.\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nSecond sentence.\n\n",
                encoding="utf-8",
            )
            service = TranslationService(
                make_config(),
                translation_client=TimeoutAfterBadTranslationClient(),
                summary_client=FakeLLMClient(),
            )

            result = service.translate(
                TranslationRequest(
                    input_file=str(input_path),
                    output_file=str(output_path),
                    target_language="Chinese",
                )
            )

            output_text = output_path.read_text(encoding="utf-8")
            self.assertNotIn("Translation missing line", output_text)
            self.assertIn("Hello there.", output_text)
            self.assertIn("Second sentence.", output_text)
            self.assertEqual(len(result.report.failed_chunks), 1)

    def test_review_port_stops_when_translation_aborts(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHello there.\n\n",
                encoding="utf-8",
            )
            config = make_config()
            config.pipeline.fallback_on_chunk_error = "abort"
            review_port = StoppableReviewPort()
            service = TranslationService(
                config,
                translation_client=FailingTranslationClient(),
                summary_client=FakeLLMClient(),
                review_port=review_port,
            )

            with self.assertRaises(RuntimeError):
                service.translate(
                    TranslationRequest(
                        input_file=str(input_path),
                        output_file=str(output_path),
                        target_language="Chinese",
                    )
                )

            self.assertTrue(review_port.stopped)

    def test_translation_defaults_output_path_from_input_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                input_path = Path(tmp) / "Demo Video.en.srt"
                input_path.write_text(
                    "1\n00:00:01,000 --> 00:00:02,000\nHello world.\n\n",
                    encoding="utf-8",
                )
                service = TranslationService(
                    make_config(),
                    translation_client=FakeLLMClient(),
                    summary_client=FakeLLMClient(),
                )

                result = service.translate(
                    TranslationRequest(
                        input_file=str(input_path),
                        output_file=None,
                        target_language="Chinese",
                        source_language="en",
                    )
                )

                output_path = Path("data/output/Demo Video.zh.srt")
                self.assertEqual(result.report.output_file, str(output_path))
                self.assertTrue(output_path.exists())
            finally:
                os.chdir(previous_cwd)

    def test_quality_gate_marks_placeholder_translation(self):
        from subtitle_llm.domain import SubtitleEntry

        entry = SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "This is a long enough subtitle line.", "Translated text")
        self.assertTrue(QualityGate().mark_entries_for_retranslation([entry]))
        self.assertTrue(entry.needs_retranslation)

    def test_quality_gate_aggregates_cascade_report(self):
        from subtitle_llm.domain import SubtitleEntry

        entries = [
            SubtitleEntry(index, "00:00:00,000", "00:00:01,000", "Original subtitle line.", "正常翻译")
            for index in range(1, 9)
        ]
        for index in range(3, 9):
            entries[index - 1].translated_text = f"Translation missing line - {index}"

        diagnosis = QualityGate().diagnose_chunk(entries, target_language="Chinese")
        report = diagnosis.to_prompt_report()

        self.assertEqual(diagnosis.reliability, "very_low")
        self.assertIn("[3]-[8] are consecutive flagged entries", report)
        self.assertIn("previous translation is broadly unreliable", report)
        self.assertIn("Ignore the previous translation", report)

    def test_quality_gate_detects_programmatic_issue_types(self):
        from subtitle_llm.domain import SubtitleEntry

        entries = [
            SubtitleEntry(1, "a", "b", "This is a source line for missing translation.", ""),
            SubtitleEntry(2, "a", "b", "This is a source line for placeholder translation.", "Translation missing line - 2"),
            SubtitleEntry(3, "a", "b", "I don't know.", "。？！"),
            SubtitleEntry(
                4,
                "a",
                "b",
                "This model is designed to handle long-context reasoning across multiple tool calls.",
                "模型",
            ),
            SubtitleEntry(5, "a", "b", "This source line should not produce a huge explanatory translation.", "很长" * 50),
            SubtitleEntry(6, "a", "b", "Let's deploy it now.", "Let's deploy it now."),
            SubtitleEntry(7, "a", "b", "Use version 2.1.0 in 2026.", "使用这个版本。"),
            SubtitleEntry(8, "a", "b", "Run `npm install` before starting app.", "启动前运行安装。"),
            SubtitleEntry(9, "a", "b", "First unique source about alpha.", "相同的翻译内容"),
            SubtitleEntry(10, "a", "b", "Second unique source about beta.", "相同的翻译内容"),
            SubtitleEntry(11, "a", "b", "Third unique source about gamma.", "相同的翻译内容"),
        ]

        diagnosis = QualityGate().diagnose_chunk(entries, target_language="Chinese")
        issue_types = {issue.issue_type for issue in diagnosis.issues}

        self.assertIn("missing_translation", issue_types)
        self.assertIn("placeholder_translation", issue_types)
        self.assertIn("punctuation_only", issue_types)
        self.assertIn("too_short", issue_types)
        self.assertIn("too_long", issue_types)
        self.assertIn("source_copied", issue_types)
        self.assertIn("target_language_mismatch", issue_types)
        self.assertIn("number_mismatch", issue_types)
        self.assertIn("url_or_code_loss", issue_types)
        self.assertIn("duplicate_translation", issue_types)

    def test_quality_gate_accepts_chinese_number_equivalents(self):
        from subtitle_llm.domain import SubtitleEntry

        entries = [
            SubtitleEntry(
                1,
                "a",
                "b",
                "It means the world 150,000 of you subscribe to the channel in a few weeks.",
                "这对我来说意义重大，短短几周内就有15万人订阅了我的频道。",
            ),
            SubtitleEntry(
                2,
                "a",
                "b",
                "There were hundreds of these little inns by the 1530s.",
                "到1530年代，伦敦有几百家这样的小旅店。",
            ),
        ]

        diagnosis = QualityGate().diagnose_chunk(entries, target_language="Chinese")

        self.assertNotIn("number_mismatch", {issue.issue_type for issue in diagnosis.issues})

    def test_quality_gate_distinguishes_decade_from_bare_year(self):
        from subtitle_llm.domain import SubtitleEntry

        entries = [
            SubtitleEntry(
                1,
                "a",
                "b",
                "There were hundreds of these little inns by the 1530s.",
                "到1530年，伦敦有几百家这样的小旅店。",
            ),
        ]

        diagnosis = QualityGate().diagnose_chunk(entries, target_language="Chinese")

        self.assertIn("number_mismatch", {issue.issue_type for issue in diagnosis.issues})

    def test_retranslate_prompt_includes_quality_report(self):
        from subtitle_llm.domain import SubtitleEntry

        client = RecordingLLMClient()
        translator = ChunkTranslator(client, make_config().translation_model)
        usage = CompletionUsage()
        quality_report = "Chunk reliability: low. [1] contains placeholder text."

        translator.re_translate(
            [SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "Hello world.", "Translation missing line - 1")],
            "[1]\nTranslation missing line - 1",
            "Chinese",
            usage,
            quality_report=quality_report,
        )

        prompt = client.prompts[-1]
        self.assertIn("Previous flawed translation", prompt)
        self.assertIn("Quality diagnosis of the previous translation", prompt)
        self.assertIn(quality_report, prompt)

    def test_tui_alignment_drift_uses_anchor_prompt_and_updates_chunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nStable first sentence.\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nSecond sentence drifts.\n\n"
                "3\n00:00:05,000 --> 00:00:06,000\nThird sentence follows.\n\n",
                encoding="utf-8",
            )
            config = make_config()
            config.pipeline.review_mode = "tui"
            config.pipeline.chunk_size = 3
            config.pipeline.refine_translation = True
            client = AlignmentDriftClient()
            review_port = DriftReviewPort()
            service = TranslationService(
                config,
                translation_client=client,
                summary_client=client,
                review_port=review_port,
            )

            result = service.translate(
                TranslationRequest(
                    input_file=str(input_path),
                    output_file=str(output_path),
                    target_language="Chinese",
                )
            )

            output_text = output_path.read_text(encoding="utf-8")
            drift_prompt = next(prompt for prompt in client.prompts if "alignment drift point" in prompt)
            self.assertIn("Stable alignment anchors", drift_prompt)
            self.assertIn("[global 1]", drift_prompt)
            self.assertIn("Previous flawed translation for the drift range", drift_prompt)
            self.assertIn("漂移修复一", output_text)
            self.assertIn("漂移修复二", output_text)
            self.assertEqual(review_port.calls, 1)
            self.assertTrue(review_port.stopped)
            self.assertEqual(result.report.failed_chunks, [])

    def test_tui_merge_accept_removes_merged_rows_from_final_subtitle(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nFirst sentence.\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nSecond sentence.\n\n"
                "3\n00:00:05,000 --> 00:00:06,000\nThird sentence.\n\n",
                encoding="utf-8",
            )
            config = make_config()
            config.pipeline.review_mode = "tui"
            config.pipeline.chunk_size = 3
            config.pipeline.semantic_translation = "off"
            config.pipeline.refine_translation = True
            client = AlignmentDriftClient()
            review_port = MergeAcceptReviewPort()
            service = TranslationService(
                config,
                translation_client=client,
                summary_client=client,
                review_port=review_port,
            )

            result = service.translate(
                TranslationRequest(
                    input_file=str(input_path),
                    output_file=str(output_path),
                    target_language="Chinese",
                )
            )

            output_text = output_path.read_text(encoding="utf-8")
            self.assertEqual(review_port.calls, 1)
            self.assertTrue(review_port.stopped)
            self.assertEqual(len(result.subtitle.entries), 1)
            self.assertEqual(result.subtitle.entries[0].start_time, "00:00:01,000")
            self.assertEqual(result.subtitle.entries[0].end_time, "00:00:06,000")
            self.assertEqual(
                result.subtitle.entries[0].original_text,
                "First sentence. Second sentence. Third sentence.",
            )
            self.assertIn("First sentence. Second sentence. Third sentence.", output_text)
            self.assertNotIn("\n2\n00:00:03,000 --> 00:00:04,000", output_text)

    def test_task_record_resume_preserves_tui_merge_removals(self):
        from subtitle_llm.domain import Subtitle, SubtitleEntry
        from subtitle_llm.pipeline.report import TranslationReport

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text("checkpoint source", encoding="utf-8")
            task_store = TranslationTaskStore(Path(tmp) / "tasks.sqlite3")
            record = task_store.create_task(
                input_display=str(input_path),
                working_directory=tmp,
                source_subtitle_path=str(input_path),
                normalized_input_fingerprint=file_fingerprint(input_path),
                target_language="Chinese",
                source_language="en",
                output_format="source-first",
                output_file=str(output_path),
                config=make_config(),
            )
            subtitle = Subtitle([
                SubtitleEntry(1, "00:00:01,000", "00:00:02,000", "First sentence.", "旧译文一"),
                SubtitleEntry(2, "00:00:03,000", "00:00:04,000", "Second sentence.", "旧译文二"),
                SubtitleEntry(3, "00:00:05,000", "00:00:06,000", "Third sentence.", "旧译文三"),
            ])
            merged_entry = SubtitleEntry(
                1,
                "00:00:01,000",
                "00:00:06,000",
                "First sentence. Second sentence. Third sentence.",
                "合并译文",
            )
            report = TranslationReport(
                input_file=str(input_path),
                output_file=str(output_path),
                context_file=str(Path(tmp) / "context.txt"),
                task_id=record.task_id,
                task_db_file=str(task_store.db_path),
            )
            ledger = RunLedger(removed_entry_indices={2, 3})

            ledger.save_task_state(task_store, record.task_id, subtitle, report, [merged_entry])

            resumed_subtitle = Subtitle([
                SubtitleEntry(1, "00:00:01,000", "00:00:02,000", "First sentence."),
                SubtitleEntry(2, "00:00:03,000", "00:00:04,000", "Second sentence."),
                SubtitleEntry(3, "00:00:05,000", "00:00:06,000", "Third sentence."),
            ])
            resume_report = TranslationReport(
                input_file=str(input_path),
                output_file=str(output_path),
                context_file=str(Path(tmp) / "context.txt"),
                task_id=record.task_id,
                task_db_file=str(task_store.db_path),
            )
            restore = RunLedger.restore_task_state(
                resume=True,
                task_id=record.task_id,
                subtitle=resumed_subtitle,
                task_store=task_store,
                report=resume_report,
            )

            self.assertEqual(restore.resumed_indices, {1})
            self.assertEqual(restore.ledger.removed_entry_indices, {2, 3})
            self.assertEqual(resume_report.removed_entry_indices, [2, 3])
            self.assertEqual(len(resumed_subtitle.entries), 1)
            self.assertEqual(resumed_subtitle.entries[0].end_time, "00:00:06,000")
            self.assertEqual(
                resumed_subtitle.entries[0].original_text,
                "First sentence. Second sentence. Third sentence.",
            )
            self.assertEqual(resumed_subtitle.entries[0].translated_text, "合并译文")

    def test_task_record_rejects_mismatched_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            input_path.write_text("data", encoding="utf-8")
            task_store = TranslationTaskStore(Path(tmp) / "tasks.sqlite3")
            record = task_store.create_task(
                input_display=str(input_path),
                working_directory=tmp,
                source_subtitle_path=str(input_path),
                normalized_input_fingerprint=file_fingerprint(input_path),
                target_language="Chinese",
                source_language="en",
                output_format="source-first",
                output_file=str(Path(tmp) / "out.srt"),
                config=make_config(),
            )

            with self.assertRaises(TranslationTaskMismatch):
                task_store.validate_task(
                    record,
                    normalized_input_fingerprint="different",
                    target_language="Chinese",
                    output_format="source-first",
                    output_file=str(Path(tmp) / "out.srt"),
                )

    def test_tui_adapter_applies_merge_map_contract(self):
        from subtitle_llm.domain import SubtitleEntry

        class FakeManager:
            def submit_chunk(self, data, chunk_index, total_chunks, completed_chunks=0):
                return {
                    "selected_subtitle_entries": [
                        {
                            "index": 1,
                            "start_time": "00:00:00,000",
                            "end_time": "00:00:02,000",
                            "original_text": "Hello world",
                            "translated_text": "",
                            "needs_retranslation": True,
                        }
                    ],
                    "merge_map": [{"merged_index": 1, "merged_from_indices": [1, 2]}],
                    "alignment_drift_start_index": 1,
                }

        port = TuiReviewPort.__new__(TuiReviewPort)
        port.manager = cast(Any, FakeManager())
        result = port.review(
            [
                SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "Hello"),
                SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "world"),
            ],
            0,
            1,
        )

        self.assertEqual(len(result.chunk), 1)
        self.assertEqual(result.chunk[0].original_text, "Hello world")
        self.assertEqual(len(result.entries_to_retranslate), 0)
        self.assertEqual(result.alignment_drift_start_index, 1)
        self.assertIsNone(result.cascade_start_index)

    def test_tui_adapter_reads_cascade_start_index(self):
        from subtitle_llm.domain import SubtitleEntry

        class FakeManager:
            def submit_chunk(self, data, chunk_index, total_chunks, completed_chunks=0):
                return {
                    "selected_subtitle_entries": [
                        {
                            "index": 2,
                            "start_time": "00:00:01,000",
                            "end_time": "00:00:02,000",
                            "original_text": "Second",
                            "translated_text": "",
                            "needs_retranslation": True,
                        },
                        {
                            "index": 3,
                            "start_time": "00:00:02,000",
                            "end_time": "00:00:03,000",
                            "original_text": "Third",
                            "translated_text": "",
                            "needs_retranslation": True,
                        },
                    ],
                    "merge_map": [],
                    "alignment_drift_start_index": None,
                    "cascade_start_index": 2,
                }

        port = TuiReviewPort.__new__(TuiReviewPort)
        port.manager = cast(Any, FakeManager())
        result = port.review(
            [
                SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "First"),
                SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "Second"),
                SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "Third"),
            ],
            0,
            1,
        )

        self.assertEqual(result.cascade_start_index, 2)
        self.assertIsNone(result.alignment_drift_start_index)
        self.assertEqual([entry.index for entry in result.entries_to_retranslate], [2, 3])

    def test_tui_adapter_infers_cascade_start_for_legacy_payload(self):
        from subtitle_llm.domain import SubtitleEntry

        class FakeManager:
            def submit_chunk(self, data, chunk_index, total_chunks, completed_chunks=0):
                return {
                    "selected_subtitle_entries": [
                        {
                            "index": 2,
                            "start_time": "00:00:01,000",
                            "end_time": "00:00:02,000",
                            "original_text": "Second",
                            "translated_text": "",
                            "needs_retranslation": True,
                        },
                    ],
                    "merge_map": [],
                    "alignment_drift_start_index": None,
                }

        port = TuiReviewPort.__new__(TuiReviewPort)
        port.manager = cast(Any, FakeManager())
        result = port.review(
            [
                SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "First"),
                SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "Second"),
            ],
            0,
            1,
        )

        self.assertEqual(result.cascade_start_index, 2)

    def test_tui_adapter_merges_translation_text_across_index_changes(self):
        from subtitle_llm.domain import SubtitleEntry

        class FakeManager:
            def submit_chunk(self, data, chunk_index, total_chunks, completed_chunks=0):
                return {
                    "selected_subtitle_entries": [],
                    "merge_map": [
                        {"merged_index": 1, "merged_from_indices": [1, 2]},
                        {"merged_index": 1, "merged_from_indices": [1, 3]},
                    ],
                    "alignment_drift_start_index": None,
                }

        port = TuiReviewPort.__new__(TuiReviewPort)
        port.manager = cast(Any, FakeManager())
        result = port.review(
            [
                SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "Hello", "你好"),
                SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "world", "世界"),
                SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "again", "又来了"),
            ],
            0,
            1,
        )

        self.assertEqual(len(result.chunk), 1)
        self.assertEqual(result.chunk[0].index, 1)
        self.assertEqual(result.chunk[0].end_time, "00:00:03,000")
        self.assertEqual(result.chunk[0].original_text, "Hello world again")
        self.assertEqual(result.chunk[0].translated_text, "你好 世界 又来了")
        self.assertEqual(result.entries_to_retranslate, [])
        self.assertEqual(result.removed_entry_indices, [2, 3])


class TestRunUsageSnapshot(unittest.TestCase):
    def test_runner_accumulates_run_usage_across_calls(self):
        from subtitle_llm.domain import SubtitleEntry
        from subtitle_llm.pipeline.llm_operations import LlmOperationRunner
        from subtitle_llm.progress_events import ProgressEmitter
        from subtitle_llm.settings import ModelConfig, ModelProvider

        emitter = ProgressEmitter("translate")
        runner = LlmOperationRunner(
            client=FakeLLMClient(),
            model_config=ModelConfig(
                type=ModelProvider.CUSTOM, api_key_env="FAKE_KEY", model="fake", endpoint="https://fake.test"
            ),
            progress=emitter,
            total_chunks=1,
        )
        chunk = [SubtitleEntry(1, "00:00:01,000", "00:00:02,000", "Hello")]

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            runner.create_completion("翻 1 条", stage="rough", chunk=chunk, chunk_index=0)
            runner.create_completion("翻 1 条", stage="rough", chunk=chunk, chunk_index=0)

        events = [
            json.loads(line[len(PROGRESS_EVENT_PREFIX):])
            for line in buffer.getvalue().splitlines()
            if line.startswith(PROGRESS_EVENT_PREFIX)
        ]
        run_usage_events = [event["run_usage"] for event in events if event.get("run_usage")]
        self.assertEqual(len(run_usage_events), 2)
        self.assertEqual(run_usage_events[0]["call_count"], 1)
        self.assertEqual(run_usage_events[1]["call_count"], 2)
        # FakeLLMClient 每次 completion_tokens=2 / total_tokens=4
        self.assertEqual(run_usage_events[1]["completion_tokens"], 4)
        self.assertEqual(run_usage_events[1]["total_tokens"], 8)
        self.assertGreaterEqual(run_usage_events[1]["call_duration_ms"], run_usage_events[0]["call_duration_ms"])


class TestReuseSubtitle(unittest.TestCase):
    def test_reuse_subtitle_skips_download_and_asr(self):
        # URL 输入 + 复用字幕：不触发 yt-dlp 下载/ASR，直接翻译复用文件。
        # 若误走下载路径，伪 URL 会让用例失败。
        with tempfile.TemporaryDirectory() as tmp:
            reused_path = Path(tmp) / "reused.en.srt"
            reused_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHello world.\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nThis is a second line.\n\n",
                encoding="utf-8",
            )
            output_path = Path(tmp) / "output.zh.srt"
            service = TranslationService(
                make_config(),
                translation_client=FakeLLMClient(),
                summary_client=FakeLLMClient(),
            )

            result = service.translate(
                TranslationRequest(
                    input_file="https://example.test/watch?v=nonexistent",
                    output_file=str(output_path),
                    target_language="Chinese",
                    reuse_subtitle=str(reused_path),
                )
            )

            output_text = output_path.read_text(encoding="utf-8")
            self.assertIn("译文1", output_text)
            self.assertEqual(result.report.input_file, str(reused_path))
            self.assertEqual(result.report.total_entries, 2)

    def test_reuse_subtitle_missing_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = TranslationService(
                make_config(),
                translation_client=FakeLLMClient(),
                summary_client=FakeLLMClient(),
            )
            with self.assertRaisesRegex(RuntimeError, "复用字幕不存在"):
                service.translate(
                    TranslationRequest(
                        input_file="https://example.test/watch?v=nonexistent",
                        output_file=str(Path(tmp) / "output.zh.srt"),
                        target_language="Chinese",
                        reuse_subtitle=str(Path(tmp) / "missing.srt"),
                    )
                )


if __name__ == "__main__":
    unittest.main()
