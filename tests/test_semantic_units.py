import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.llm.types import CompletionResult, CompletionUsage
from subtitle_llm.pipeline import TranslationRequest, TranslationService
from subtitle_llm.pipeline.chunk_translator import ChunkTranslationResult, TracedTranslationText
from subtitle_llm.pipeline.chunks import PlannedChunk
from subtitle_llm.pipeline.quality import QualityGate
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.semantic_units import (
    apply_semantic_translation,
    build_semantic_units,
    semantic_entries,
)
from subtitle_llm.review.ports import ReviewResult
from subtitle_llm.settings import AppConfig, ModelConfig, ModelProvider, PipelineConfig


class SemanticTranslationClient:
    def create_completion(self, config, messages):
        prompt = messages[-1]["content"]
        if "Analyze the following subtitle content" in prompt:
            return CompletionResult(
                content="总结: demo summary\n\n短语术语:\n- Grill Me(追问我)",
                usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

        body = (
            '{"translations": ['
            '{"unit_id": 1, "translation": "几个月前我写了几句话后来影响很大。"}, '
            '{"unit_id": 2, "translation": "我把这些句子打包成追问我技能。"}'
            "]}"
        )
        return CompletionResult(
            content=body,
            usage=CompletionUsage(prompt_tokens=2, completion_tokens=2, total_tokens=4),
        )


def make_config(review_mode: str = "auto") -> AppConfig:
    model = ModelConfig(type=ModelProvider.CUSTOM, api_key_env="FAKE_KEY", model="fake", endpoint="https://fake.test")
    return AppConfig(
        summary_model=model,
        translation_model=model,
        pipeline=PipelineConfig(
            chunk_size=4,
            threads=1,
            context_window_size=1,
            review_mode=review_mode,
            normalize_subtitles="off",
            semantic_translation="auto",
        ),
    )


class NoopReviewPort:
    def review(self, chunk, chunk_index, total_chunks, completed_chunks=0):
        raise AssertionError("review should not be called for clean semantic output")

    def stop(self):
        pass


class RecordingSemanticTranslator:
    progress = None
    trace_recorder = None

    def __init__(self):
        self.recorded_source_texts: list[str] = []
        self.drift_source_texts: list[str] = []
        self.drift_anchor_texts: list[str] = []

    def translate_semantic_and_refine(
        self,
        chunk,
        context,
        target_language,
        boundary_context,
        chunk_index=None,
        stage_prefix="",
    ):
        self.recorded_source_texts = [entry.original_text for entry in chunk]
        return ChunkTranslationResult(
            chunk=chunk,
            translation="[1]\n完整语义译文。",
            usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            final_trace_id="trace-semantic",
        )

    def retranslate_alignment_drift_traced(
        self,
        drift_chunk,
        stable_anchors,
        context,
        target_language,
        boundary_context,
        usage,
        chunk_index=None,
    ):
        self.drift_source_texts = [entry.original_text for entry in drift_chunk]
        self.drift_anchor_texts = [entry.original_text for entry in stable_anchors]
        usage.add(CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2))
        return TracedTranslationText("[1]\n漂移修复一\n[2]\n漂移修复二", "trace-drift")


class TestSemanticUnits(unittest.TestCase):
    def test_builds_units_from_split_sentences(self):
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "A few months ago,"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "I wrote a few sentences"),
            SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "that mattered."),
            SubtitleEntry(4, "00:00:03,000", "00:00:04,000", "I packaged them"),
            SubtitleEntry(5, "00:00:04,000", "00:00:05,000", "into a skill."),
        ]

        units = build_semantic_units(entries)

        self.assertEqual([unit.cue_indices for unit in units], [[1, 2, 3], [4, 5]])
        self.assertEqual(
            units[0].source_text,
            "A few months ago, I wrote a few sentences that mattered.",
        )

    def test_splits_semantic_translation_back_to_original_cues(self):
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "A few months ago,"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "I wrote a few sentences"),
            SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "that mattered."),
        ]
        unit = build_semantic_units(entries)[0]

        updated = apply_semantic_translation(
            unit,
            "几个月前我写了几句话后来影响很大。",
            target_language="Chinese",
        )

        self.assertEqual(
            "".join(entry.translated_text for entry in updated),
            "几个月前我写了几句话后来影响很大。",
        )
        self.assertTrue(all(entry.translated_text for entry in updated))

    def test_pipeline_translates_semantic_units_then_maps_to_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nA few months ago,\n\n"
                "2\n00:00:01,000 --> 00:00:02,000\nI wrote a few sentences\n\n"
                "3\n00:00:02,000 --> 00:00:03,000\nthat mattered.\n\n"
                "4\n00:00:03,000 --> 00:00:04,000\nI packaged them\n\n"
                "5\n00:00:04,000 --> 00:00:05,000\ninto a skill.\n\n",
                encoding="utf-8",
            )
            service = TranslationService(
                make_config(),
                translation_client=SemanticTranslationClient(),
                summary_client=SemanticTranslationClient(),
            )

            result = service.translate(
                TranslationRequest(
                    input_file=str(input_path),
                    output_file=str(output_path),
                    target_language="Chinese",
                )
            )

            self.assertTrue(result.report.semantic_translation_applied)
            self.assertEqual(result.report.semantic_units, 2)
            self.assertEqual(result.report.semantic_multi_cue_units, 2)
            self.assertEqual(len(result.subtitle.entries), 5)
            self.assertEqual(
                "".join(entry.translated_text for entry in result.subtitle.entries[:3]),
                "几个月前我写了几句话后来影响很大。",
            )
            self.assertEqual(
                "".join(entry.translated_text for entry in result.subtitle.entries[3:]),
                "我把这些句子打包成追问我技能。",
            )

    def test_tui_mode_still_translates_with_semantic_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nA few months ago,\n\n"
                "2\n00:00:01,000 --> 00:00:02,000\nI wrote a few sentences\n\n"
                "3\n00:00:02,000 --> 00:00:03,000\nthat mattered.\n\n"
                "4\n00:00:03,000 --> 00:00:04,000\nI packaged them\n\n"
                "5\n00:00:04,000 --> 00:00:05,000\ninto a skill.\n\n",
                encoding="utf-8",
            )
            service = TranslationService(
                make_config(review_mode="tui"),
                translation_client=SemanticTranslationClient(),
                summary_client=SemanticTranslationClient(),
                review_port=NoopReviewPort(),
            )

            result = service.translate(
                TranslationRequest(
                    input_file=str(input_path),
                    output_file=str(output_path),
                    target_language="Chinese",
                    review_mode="tui",
                )
            )

            self.assertTrue(result.report.semantic_translation_applied)
            self.assertEqual(result.report.semantic_multi_cue_units, 2)

    def test_tui_retranslation_expands_selected_cue_to_semantic_unit(self):
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "A few months ago,"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "I wrote a few sentences"),
            SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "that mattered."),
        ]
        units = build_semantic_units(entries)
        planned = PlannedChunk(index=0, entries=semantic_entries(units), boundary_context="", boundary_risks=[])
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            checkpoint_file="checkpoint.json",
            context_file="context.txt",
            total_chunks=1,
        )
        translator = RecordingSemanticTranslator()
        service = TranslationService(
            make_config(),
            translation_client=SemanticTranslationClient(),
            summary_client=SemanticTranslationClient(),
        )
        review_result = ReviewResult(
            chunk=entries,
            entries_to_retranslate=[entries[1]],
        )

        outcome = service._apply_semantic_tui_review_result(
            planned,
            entries,
            units,
            {entry.index: unit for unit in units for entry in unit.entries},
            review_result,
            translator,
            context="",
            target_language="Chinese",
            report=report,
        )

        self.assertTrue(outcome.retranslated)
        self.assertEqual(
            translator.recorded_source_texts,
            ["A few months ago, I wrote a few sentences that mattered."],
        )
        self.assertEqual(
            "".join(entry.translated_text for entry in entries),
            "完整语义译文。",
        )

    def test_tui_cascade_uses_full_semantic_context_but_writes_back_from_start(self):
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "A few months ago,", "旧译文一"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "I wrote a few sentences", "旧译文二", True),
            SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "that mattered.", "旧译文三", True),
        ]
        units = build_semantic_units(entries)
        planned = PlannedChunk(index=0, entries=semantic_entries(units), boundary_context="", boundary_risks=[])
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            checkpoint_file="checkpoint.json",
            context_file="context.txt",
            total_chunks=1,
        )
        translator = RecordingSemanticTranslator()
        service = TranslationService(
            make_config(),
            translation_client=SemanticTranslationClient(),
            summary_client=SemanticTranslationClient(),
        )
        review_result = ReviewResult(
            chunk=entries,
            entries_to_retranslate=[entries[1], entries[2]],
            cascade_start_index=2,
        )

        outcome = service._apply_semantic_tui_review_result(
            planned,
            entries,
            units,
            {entry.index: unit for unit in units for entry in unit.entries},
            review_result,
            translator,
            context="",
            target_language="Chinese",
            report=report,
        )

        self.assertTrue(outcome.retranslated)
        self.assertEqual(
            translator.recorded_source_texts,
            ["A few months ago, I wrote a few sentences that mattered."],
        )
        self.assertEqual(entries[0].translated_text, "旧译文一")
        self.assertNotEqual(entries[1].translated_text, "旧译文二")
        self.assertNotEqual(entries[2].translated_text, "旧译文三")

    def test_tui_semantic_alignment_drift_uses_drift_prompt_range(self):
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "A few months ago,", "旧译文一"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "I wrote a few sentences", "旧译文二", True),
            SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "that mattered.", "旧译文三", True),
        ]
        units = build_semantic_units(entries)
        planned = PlannedChunk(index=0, entries=semantic_entries(units), boundary_context="", boundary_risks=[])
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            checkpoint_file="checkpoint.json",
            context_file="context.txt",
            total_chunks=1,
        )
        translator = RecordingSemanticTranslator()
        service = TranslationService(
            make_config(),
            translation_client=SemanticTranslationClient(),
            summary_client=SemanticTranslationClient(),
        )
        review_result = ReviewResult(
            chunk=entries,
            entries_to_retranslate=[],
            alignment_drift_start_index=2,
        )

        outcome = service._apply_semantic_tui_review_result(
            planned,
            entries,
            units,
            {entry.index: unit for unit in units for entry in unit.entries},
            review_result,
            translator,
            context="",
            target_language="Chinese",
            report=report,
        )

        self.assertTrue(outcome.retranslated)
        self.assertEqual(translator.recorded_source_texts, [])
        self.assertEqual(translator.drift_anchor_texts, ["A few months ago,"])
        self.assertEqual(translator.drift_source_texts, ["I wrote a few sentences", "that mattered."])
        self.assertEqual(entries[0].translated_text, "旧译文一")
        self.assertEqual(entries[1].translated_text, "漂移修复一")
        self.assertEqual(entries[2].translated_text, "漂移修复二")

    def test_quality_gate_flags_carry_over_commentary(self):
        entry = SubtitleEntry(
            1,
            "00:00:00,000",
            "00:00:01,000",
            "won't.",
            "（承接上文）有些问题则不行。",
        )

        diagnosis = QualityGate().diagnose_chunk([entry], target_language="Chinese")

        self.assertTrue(diagnosis.has_issues)
        self.assertEqual(diagnosis.issues[0].issue_type, "commentary_marker")


if __name__ == "__main__":
    unittest.main()
