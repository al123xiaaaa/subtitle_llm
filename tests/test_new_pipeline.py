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
from subtitle_llm.pipeline.checkpoint import CheckpointMismatch, CheckpointStore, file_fingerprint
from subtitle_llm.pipeline.quality import QualityGate
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
            self.assertTrue(Path(result.report.checkpoint_file).exists())

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

    def test_checkpoint_rejects_mismatched_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            input_path.write_text("data", encoding="utf-8")
            checkpoint_path = Path(tmp) / "checkpoint.json"
            store = CheckpointStore(
                checkpoint_path,
                file_fingerprint(input_path),
                "Chinese",
                "source-first",
                "2",
            )
            from subtitle_llm.domain import Subtitle, SubtitleEntry
            from subtitle_llm.pipeline.report import TranslationReport

            subtitle = Subtitle([SubtitleEntry(1, "a", "b", "hello", "你好")])
            report = TranslationReport(
                input_file=str(input_path),
                output_file=str(Path(tmp) / "out.srt"),
                checkpoint_file=str(checkpoint_path),
                context_file=str(Path(tmp) / "context.txt"),
            )
            store.save(subtitle, report)

            bad_store = CheckpointStore(checkpoint_path, "different", "Chinese", "source-first", "2")
            with self.assertRaises(CheckpointMismatch):
                bad_store.load()

    def test_tui_adapter_applies_merge_map_contract(self):
        from subtitle_llm.domain import SubtitleEntry

        class FakeManager:
            def submit_chunk(self, data, chunk_index, total_chunks):
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
        self.assertEqual(len(result.entries_to_retranslate), 1)


if __name__ == "__main__":
    unittest.main()
