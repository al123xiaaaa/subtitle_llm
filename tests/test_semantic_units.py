import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Literal, cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.llm.types import CompletionResult, CompletionUsage
from subtitle_llm.pipeline import TranslationRequest, TranslationService
from subtitle_llm.pipeline.checkpoint import CheckpointStore, file_fingerprint, sidecar_path
from subtitle_llm.pipeline.chunk_translator import ChunkTranslationResult, TracedTranslationText
from subtitle_llm.pipeline.chunks import PlannedChunk
from subtitle_llm.pipeline.quality import QualityGate
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.run_ledger import RunLedger
from subtitle_llm.pipeline.semantic_layout import diagnose_layout_pair, is_orphan_punctuation
from subtitle_llm.pipeline.semantic_units import (
    apply_semantic_translation,
    build_semantic_units,
    semantic_entries,
    split_translation,
)
from subtitle_llm.pipeline.text import parse_indexed_translation_for_entries
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


class CueAwareSemanticClient:
    def __init__(self):
        self.prompts: list[str] = []

    def create_completion(self, config, messages):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if "Analyze the following subtitle content" in prompt:
            return CompletionResult(
                content="总结: demo summary\n\n短语术语:\n- Grill Me(追问我)",
                usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

        body = (
            '{"translations": ['
            '{"cue_id": 1, "translation": "几个月前，"}, '
            '{"cue_id": 2, "translation": "我写了几句话"}, '
            '{"cue_id": 3, "translation": "后来影响很大。"}'
            "]}"
        )
        return CompletionResult(
            content=body,
            usage=CompletionUsage(prompt_tokens=2, completion_tokens=2, total_tokens=4),
        )


class SourceCorrectionSemanticClient:
    def __init__(self):
        self.prompts: list[str] = []

    def create_completion(self, config, messages):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if "Analyze the following subtitle content" in prompt:
            return CompletionResult(
                content=(
                    '{"summary": "讲述都铎伦敦的街道。", '
                    '"terms": [{"source": "Cheapside", "target": "齐普赛街"}], '
                    '"source_corrections": ['
                    '{"cue_ids": [99], "observed": "cheap side", "corrected": "Cheapside", '
                    '"type": "street", "enforcement": "hard", '
                    '"target_aliases": ["齐普赛街", "Cheapside"], "confidence": "high", '
                    '"evidence": "The source calls it the main market street of Tudor London."}'
                    "]}"
                ),
                usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
        if "Repair exactly one timed subtitle cue" in prompt:
            return CompletionResult(
                content='{"cue_id": 2, "translation": "所以，齐普赛街。都铎伦敦的主要市场大街。"}',
                usage=CompletionUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
            )

        return CompletionResult(
            content=(
                '{"translations": ['
                '{"cue_id": 1, "translation": "我到达了都铎伦敦，"}, '
                '{"cue_id": 2, "translation": "所以，这边便宜。都铎伦敦的主要市场大街。"}'
                "]}"
            ),
            usage=CompletionUsage(prompt_tokens=2, completion_tokens=2, total_tokens=4),
        )


def make_config(review_mode: Literal["auto", "tui"] = "auto") -> AppConfig:
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


class PunctuationOrphanSemanticClient:
    def create_completion(self, config, messages):
        prompt = messages[-1]["content"]
        if "Analyze the following subtitle content" in prompt:
            return CompletionResult(
                content="总结: demo summary\n\n短语术语:\n- sentence(句子)",
                usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

        return CompletionResult(
            content='{"translations": [{"unit_id": 1, "translation": "好。"}]}',
            usage=CompletionUsage(prompt_tokens=2, completion_tokens=2, total_tokens=4),
        )


class SuspiciousSemanticClient:
    def create_completion(self, config, messages):
        prompt = messages[-1]["content"]
        if "Analyze the following subtitle content" in prompt:
            return CompletionResult(
                content="总结: demo summary\n\n短语术语:\n- Tudor London(都铎伦敦)",
                usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
        if "Previous flawed translation" in prompt:
            raise AssertionError("TUI semantic mode should not run full-chunk auto repair")
        return CompletionResult(
            content='{"translations": [{"unit_id": 1, "translation": "短。"}]}',
            usage=CompletionUsage(prompt_tokens=2, completion_tokens=2, total_tokens=4),
        )


class AcceptAllReviewPort:
    def __init__(self):
        self.calls = 0
        self.reviewed_translations: list[str] = []

    def review(self, chunk, chunk_index, total_chunks, completed_chunks=0):
        self.calls += 1
        self.reviewed_translations = [entry.translated_text for entry in chunk]
        return ReviewResult(chunk=chunk, entries_to_retranslate=[])

    def stop(self):
        pass


class RecordingSemanticTranslator:
    progress = None
    trace_recorder = None

    def __init__(self):
        self.recorded_source_texts: list[str] = []
        self.repair_briefs: list[str] = []
        self.repair_output_indices: list[int] = []
        self.repair_output_batches: list[list[int]] = []
        self.repair_stages: list[str] = []
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
        refine_translation=True,
    ):
        self.recorded_source_texts = [entry.original_text for entry in chunk]
        return ChunkTranslationResult(
            chunk=chunk,
            translation="[1]\n完整语义译文。",
            usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            final_trace_id="trace-semantic",
        )

    def repair_semantic_timed_cues_traced(
        self,
        output_entries,
        repair_brief,
        context,
        target_language,
        boundary_context,
        usage,
        chunk_index=None,
        stage="tui-semantic-repair",
    ):
        self.repair_briefs.append(repair_brief)
        self.repair_output_indices = [entry.index for entry in output_entries]
        self.repair_output_batches.append([entry.index for entry in output_entries])
        self.repair_stages.append(stage)
        if "Repair intent: alignment_drift" in repair_brief:
            self.drift_source_texts = [entry.original_text for entry in output_entries]
            anchors_section = repair_brief.split("Readonly stable anchors:", 1)[-1].split("Quality diagnosis:", 1)[0]
            self.drift_anchor_texts = [
                line.strip().removeprefix("Source: ")
                for line in anchors_section.splitlines()
                if line.strip().startswith("Source: ")
            ]
        usage.add(CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2))
        body = "\n".join(
            f"[{index}]\n修复译文{index}"
            for index, _entry in enumerate(output_entries, start=1)
        )
        return TracedTranslationText(body, "trace-repair")

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


class FailingRepairSemanticTranslator(RecordingSemanticTranslator):
    def repair_semantic_timed_cues_traced(
        self,
        output_entries,
        repair_brief,
        context,
        target_language,
        boundary_context,
        usage,
        chunk_index=None,
        stage="tui-semantic-repair",
    ):
        self.repair_briefs.append(repair_brief)
        self.repair_output_indices = [entry.index for entry in output_entries]
        self.repair_output_batches.append([entry.index for entry in output_entries])
        self.repair_stages.append(stage)
        usage.add(CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2))
        raise ValueError("translation index mismatch: missing=[2], extra=[]")


class FailLargeRepairSemanticTranslator(RecordingSemanticTranslator):
    def repair_semantic_timed_cues_traced(
        self,
        output_entries,
        repair_brief,
        context,
        target_language,
        boundary_context,
        usage,
        chunk_index=None,
        stage="tui-semantic-repair",
    ):
        if len(output_entries) > 2:
            self.repair_briefs.append(repair_brief)
            self.repair_output_indices = [entry.index for entry in output_entries]
            self.repair_output_batches.append([entry.index for entry in output_entries])
            self.repair_stages.append(stage)
            usage.add(CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2))
            raise ValueError("simulated oversized repair failure")
        return super().repair_semantic_timed_cues_traced(
            output_entries,
            repair_brief,
            context,
            target_language,
            boundary_context,
            usage,
            chunk_index=chunk_index,
            stage=stage,
        )


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

    def test_layout_split_avoids_creating_punctuation_tail(self):
        entries = [
            SubtitleEntry(
                1,
                "00:00:00,000",
                "00:00:01,000",
                "have turned out to be the most influential four sentences I've ever",
            ),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "written."),
        ]

        pieces = split_translation(
            "结果证明这是我写过的最具影响力的四句话。",
            entries,
            target_language="Chinese",
        )

        self.assertEqual(len(pieces), 2)
        self.assertFalse(any(is_orphan_punctuation(piece) for piece in pieces))
        self.assertEqual("".join(pieces), "结果证明这是我写过的最具影响力的四句话。")

    def test_layout_split_preserves_latin_tokens_and_phrases(self):
        entries = [
            SubtitleEntry(
                1,
                "00:00:00,000",
                "00:00:01,000",
                "piece of your system, consider whether that data can live in a single distributed",
            ),
            SubtitleEntry(
                2,
                "00:00:01,000",
                "00:00:02,000",
                "database like Spanner or Yugabyte DB that handle that strong consistency internally",
            ),
            SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "for you."),
        ]

        pieces = split_translation(
            "最后一点，如果系统中某个部分确实无法接受最终一致性，考虑将数据放入一个单一的分布式数据库，比如 Spanner 或 Yugabyte DB，它们内部为你处理强一致性。",
            entries,
            target_language="Chinese",
        )

        self.assertEqual(len(pieces), 3)
        self.assertFalse(any(piece.endswith("Spann") for piece in pieces))
        self.assertFalse(any(piece.startswith("er") for piece in pieces))
        self.assertIn("Spanner 或 Yugabyte DB", "".join(pieces))
        self.assertEqual(
            "".join(pieces),
            "最后一点，如果系统中某个部分确实无法接受最终一致性，考虑将数据放入一个单一的分布式数据库，比如 Spanner 或 Yugabyte DB，它们内部为你处理强一致性。",
        )

    def test_layout_split_protects_code_tokens_and_paired_punctuation(self):
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "Use HTTP/2"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "in the development environment"),
            SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "and call foo_bar."),
        ]

        pieces = split_translation(
            "请使用 HTTP/2 连接（开发）环境，然后调用 foo_bar。",
            entries,
            target_language="Chinese",
        )

        self.assertEqual(len(pieces), 3)
        self.assertEqual(
            "".join("".join(pieces).split()),
            "".join("请使用 HTTP/2 连接（开发）环境，然后调用 foo_bar。".split()),
        )
        self.assertIn("HTTP/2", " ".join(pieces))
        self.assertIn("foo_bar", " ".join(pieces))
        self.assertFalse(any(piece.endswith(("HTTP/", "foo_")) for piece in pieces))
        self.assertFalse(any(piece.startswith(("2", "bar")) for piece in pieces))

    def test_layout_split_preserves_full_unspaced_translation(self):
        entries = [
            SubtitleEntry(
                1,
                "00:00:00,000",
                "00:00:01,000",
                "You have a database, and when a customer places an order, you wrap the whole thing",
            ),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "in a transaction."),
        ]

        pieces = split_translation(
            "你有一个数据库，当客户下单时，你可以将整个过程包装在一个事务中。",
            entries,
            target_language="Chinese",
        )

        self.assertEqual(len(pieces), 2)
        self.assertFalse(any(is_orphan_punctuation(piece) for piece in pieces))
        self.assertEqual("".join(pieces), "你有一个数据库，当客户下单时，你可以将整个过程包装在一个事务中。")

    def test_recent_trace_like_layout_contract_preserves_structural_spans(self):
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "If this part of your system"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "cannot accept eventual consistency,"),
            SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "consider putting the data"),
            SubtitleEntry(4, "00:00:03,000", "00:00:04,000", "in Spanner or Yugabyte DB,"),
            SubtitleEntry(5, "00:00:04,000", "00:00:05,000", "which handle strong consistency"),
            SubtitleEntry(6, "00:00:05,000", "00:00:06,000", "inside the database."),
        ]

        pieces = split_translation(
            "如果系统中的某个部分确实无法接受最终一致性，考虑把数据放进 Spanner 或 Yugabyte DB "
            "这样的数据库，它们会在内部处理强一致性（而不是让应用层处理）。",
            entries,
            target_language="Chinese",
        )

        self.assertEqual(len(pieces), 6)
        self.assertEqual(
            "".join("".join(pieces).split()),
            "".join(
                "如果系统中的某个部分确实无法接受最终一致性，考虑把数据放进 Spanner 或 Yugabyte DB "
                "这样的数据库，它们会在内部处理强一致性（而不是让应用层处理）。".split()
            ),
        )
        self.assertIn("Spanner 或 Yugabyte DB", " ".join(pieces))
        self.assertFalse(any(piece.endswith("Spann") for piece in pieces))
        self.assertFalse(any(piece.startswith("er") for piece in pieces))

    def test_semantic_layout_does_not_auto_merge_missing_empty_translations(self):
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "I am"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "very"),
            SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "happy."),
        ]
        unit = build_semantic_units(entries)[0]
        source_entries = apply_semantic_translation(unit, "好", target_language="Chinese")
        planned = PlannedChunk(index=0, entries=semantic_entries([unit]), boundary_context="", boundary_risks=[])
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            checkpoint_file="checkpoint.json",
            context_file="context.txt",
            total_chunks=1,
        )
        run_ledger = RunLedger()

        repaired_entries = TranslationService._repair_semantic_layout(
            cast(Any, None),
            planned,
            source_entries,
            [unit],
            run_ledger,
            report,
            "Chinese",
        )
        diagnosis = QualityGate().diagnose_chunk(repaired_entries, target_language="Chinese")

        self.assertEqual([entry.index for entry in repaired_entries], [1, 2, 3])
        self.assertEqual(run_ledger.removed_entry_indices, set())
        self.assertEqual(report.auto_layout_repairs, [])
        self.assertTrue(diagnosis.has_issues)
        self.assertIn("missing_translation", {issue.issue_type for issue in diagnosis.issues})

    def test_semantic_layout_auto_merges_punctuation_tail_before_tui(self):
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "Hi", "好"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", ".", "。"),
        ]
        unit = build_semantic_units(entries)[0]
        planned = PlannedChunk(index=0, entries=semantic_entries([unit]), boundary_context="", boundary_risks=[])
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            checkpoint_file="checkpoint.json",
            context_file="context.txt",
            total_chunks=1,
        )
        run_ledger = RunLedger()

        repaired_entries = TranslationService._repair_semantic_layout(
            cast(Any, None),
            planned,
            entries,
            [unit],
            run_ledger,
            report,
            "Chinese",
        )

        self.assertEqual([entry.index for entry in repaired_entries], [1])
        self.assertEqual(repaired_entries[0].end_time, "00:00:02,000")
        self.assertEqual(repaired_entries[0].original_text, "Hi .")
        self.assertEqual(repaired_entries[0].translated_text, "好。")
        self.assertEqual(report.removed_entry_indices, [2])
        self.assertEqual(len(report.auto_layout_repairs), 1)
        self.assertEqual(report.auto_layout_repairs[0].removed_index, 2)

    def test_semantic_layout_can_mark_auto_merge_issue_without_removing_cues(self):
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "Hi", "好"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", ".", "。"),
        ]
        unit = build_semantic_units(entries)[0]
        planned = PlannedChunk(index=0, entries=semantic_entries([unit]), boundary_context="", boundary_risks=[])
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            checkpoint_file="checkpoint.json",
            context_file="context.txt",
            total_chunks=1,
        )
        run_ledger = RunLedger()

        repaired_entries = TranslationService._repair_semantic_layout(
            cast(Any, None),
            planned,
            entries,
            [unit],
            run_ledger,
            report,
            "Chinese",
            allow_auto_merge=False,
        )

        self.assertEqual([entry.index for entry in repaired_entries], [1, 2])
        self.assertTrue(all(entry.needs_retranslation for entry in repaired_entries))
        self.assertEqual(run_ledger.removed_entry_indices, set())
        self.assertEqual(report.auto_layout_repairs, [])

    def test_auto_layout_repair_persists_removed_indices_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nHi\n\n"
                "2\n00:00:01,000 --> 00:00:02,000\n.\n\n",
                encoding="utf-8",
            )
            config = make_config(review_mode="tui")
            checkpoint = CheckpointStore(
                sidecar_path(output_path, "_checkpoint.json"),
                file_fingerprint(input_path),
                "Chinese",
                "source-first",
                config.config_version,
            )
            subtitle = Subtitle([
                SubtitleEntry(1, "00:00:00,000", "00:00:02,000", "Hi .", "好。"),
            ])
            report = TranslationReport(
                input_file=str(input_path),
                output_file=str(output_path),
                checkpoint_file=str(sidecar_path(output_path, "_checkpoint.json")),
                context_file=str(sidecar_path(output_path, "_context.txt")),
                auto_layout_repairs=[],
            )
            ledger = RunLedger()
            ledger.record_auto_layout_repair(report, merged_index=1, removed_index=2, reason="punctuation_only")
            ledger.save_checkpoint(checkpoint, subtitle, report, subtitle.entries)

            checkpoint = CheckpointStore(
                sidecar_path(output_path, "_checkpoint.json"),
                file_fingerprint(input_path),
                "Chinese",
                "source-first",
                config.config_version,
            )
            resumed_subtitle = Subtitle([
                SubtitleEntry(
                    1,
                    "00:00:00,000",
                    "00:00:01,000",
                    "Hi",
                ),
                SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "."),
            ])
            resume_report = TranslationReport(
                input_file=str(input_path),
                output_file=str(output_path),
                checkpoint_file=str(sidecar_path(output_path, "_checkpoint.json")),
                context_file=str(sidecar_path(output_path, "_context.txt")),
            )

            restore = RunLedger.restore_checkpoint(
                resume=True,
                subtitle=resumed_subtitle,
                checkpoint=checkpoint,
                report=resume_report,
            )

            self.assertEqual(restore.resumed_indices, {1})
            self.assertEqual(restore.ledger.removed_entry_indices, {2})
            self.assertEqual(len(resumed_subtitle.entries), 1)
            self.assertEqual(resume_report.auto_layout_repairs[0].removed_index, 2)

    def test_semantic_layout_auto_merges_latin_token_split(self):
        entries = [
            SubtitleEntry(
                1,
                "00:00:00,000",
                "00:00:01,000",
                "database like Google",
                "两阶段提交仅限于 Google Span",
            ),
            SubtitleEntry(
                2,
                "00:00:01,000",
                "00:00:02,000",
                "Spanner or Yugabyte DB",
                "ner 或 Yugabyte DB 这类数据库内部。",
            ),
        ]
        unit = build_semantic_units(entries)[0]
        planned = PlannedChunk(index=0, entries=semantic_entries([unit]), boundary_context="", boundary_risks=[])
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            checkpoint_file="checkpoint.json",
            context_file="context.txt",
            total_chunks=1,
        )
        run_ledger = RunLedger()

        repaired_entries = TranslationService._repair_semantic_layout(
            cast(Any, None),
            planned,
            entries,
            [unit],
            run_ledger,
            report,
            "Chinese",
        )

        self.assertEqual([entry.index for entry in repaired_entries], [1])
        self.assertEqual(repaired_entries[0].end_time, "00:00:02,000")
        self.assertEqual(repaired_entries[0].translated_text, "两阶段提交仅限于 Google Spanner 或 Yugabyte DB 这类数据库内部。")
        self.assertEqual(run_ledger.removed_entry_indices, {2})
        self.assertEqual(report.auto_layout_repairs[0].reason, "latin_token_split")

    def test_semantic_layout_does_not_auto_merge_language_specific_word_fragments(self):
        examples = [
            ("处理强", "一致性。"),
            ("处理强一", "致性。"),
            ("处理最终一致", "性。"),
        ]
        for left_text, right_text in examples:
            with self.subTest(left=left_text, right=right_text):
                left = SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "left", left_text)
                right = SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "right", right_text)

                issue = diagnose_layout_pair(left, right, target_language="Chinese")

                self.assertIsNone(issue)

    def test_semantic_layout_does_not_merge_valid_language_line_start(self):
        left = SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "First point.", "我们先讨论目标。")
        right = SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "Find the problem.", "发现问题后再处理。")

        issue = diagnose_layout_pair(left, right, target_language="Chinese")

        self.assertIsNone(issue)

    def test_semantic_layout_does_not_auto_merge_language_specific_short_tails_without_structural_issue(self):
        examples = [
            (
                "shared state gets",
                "corrupted at the exact same",
                "正确性问题发生在两个线程同时访问共享状态导致状态损",
                "坏时。",
            ),
            (
                "showing up in low-level design",
                "interviews.",
                "低层设计面试中最常见的并发",
                "问题。",
            ),
            (
                "shows up is what's called read",
                "modify write.",
                "第二种最常见的正确性问题模式是所谓",
                "的“读取-修改-写入”。",
            ),
        ]
        for left_source, right_source, left_text, right_text in examples:
            with self.subTest(right=right_text):
                left = SubtitleEntry(1, "00:00:00,000", "00:00:01,000", left_source, left_text)
                right = SubtitleEntry(2, "00:00:01,000", "00:00:02,000", right_source, right_text)

                issue = diagnose_layout_pair(left, right, target_language="Chinese")

                self.assertIsNone(issue)

    def test_indexed_translation_parser_accepts_global_indices_for_repair(self):
        entries = [
            SubtitleEntry(65, "00:00:00,000", "00:00:01,000", "source"),
            SubtitleEntry(66, "00:00:01,000", "00:00:02,000", "source"),
        ]

        parsed = parse_indexed_translation_for_entries("[65]\n第一条\n[66]\n第二条", entries)

        self.assertEqual(parsed, ["第一条", "第二条"])

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
            self.assertEqual(len(result.report.auto_layout_repairs), 0)
            self.assertEqual(
                "".join(entry.translated_text for entry in result.subtitle.entries),
                "几个月前我写了几句话后来影响很大。我把这些句子打包成追问我技能。",
            )

    def test_pipeline_can_translate_semantic_context_directly_to_timed_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nA few months ago,\n\n"
                "2\n00:00:01,000 --> 00:00:02,000\nI wrote a few sentences\n\n"
                "3\n00:00:02,000 --> 00:00:03,000\nthat mattered.\n\n",
                encoding="utf-8",
            )
            client = CueAwareSemanticClient()
            service = TranslationService(
                make_config(),
                translation_client=client,
                summary_client=client,
            )

            result = service.translate(
                TranslationRequest(
                    input_file=str(input_path),
                    output_file=str(output_path),
                    target_language="Chinese",
                )
            )

            self.assertTrue(result.report.semantic_translation_applied)
            self.assertEqual(
                [entry.translated_text for entry in result.subtitle.entries],
                ["几个月前，", "我写了几句话", "后来影响很大。"],
            )
            translation_prompt = next(prompt for prompt in client.prompts if "Semantic units with timed cues" in prompt)
            self.assertIn('"cue_id": 1', translation_prompt)
            self.assertIn('"source_index": 1', translation_prompt)

    def test_pipeline_repairs_unadopted_hard_source_correction_without_tui(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nI arrived in Tudor London and\n\n"
                "2\n00:00:01,000 --> 00:00:02,000\n"
                "so cheap side.The main market street of Tudor London\n\n",
                encoding="utf-8",
            )
            client = SourceCorrectionSemanticClient()
            config = make_config()
            config.pipeline.semantic_translation = "always"
            service = TranslationService(
                config,
                translation_client=client,
                summary_client=client,
                review_port=NoopReviewPort(),
            )

            result = service.translate(
                TranslationRequest(
                    input_file=str(input_path),
                    output_file=str(output_path),
                    target_language="Chinese",
                )
            )

            self.assertEqual(
                [entry.translated_text for entry in result.subtitle.entries],
                ["我到达了都铎伦敦，", "所以，齐普赛街。都铎伦敦的主要市场大街。"],
            )
            self.assertEqual(
                result.subtitle.entries[1].original_text,
                "so cheap side.The main market street of Tudor London",
            )
            output_text = output_path.read_text(encoding="utf-8")
            self.assertIn("so Cheapside. The main market street of Tudor London", output_text)
            self.assertNotIn("so cheap side.The main market street of Tudor London", output_text)
            self.assertTrue(any("Repair exactly one timed subtitle cue" in prompt for prompt in client.prompts))
            self.assertEqual(result.report.token_usage.total_tokens, 11)

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

    def test_tui_semantic_mode_skips_full_chunk_auto_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.srt"
            output_path = Path(tmp) / "output.srt"
            input_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n"
                "Just arrived in Tudor London and I need to describe a crowded market before meeting the king.\n\n",
                encoding="utf-8",
            )
            review_port = AcceptAllReviewPort()
            config = make_config(review_mode="tui")
            config.pipeline.semantic_translation = "always"
            service = TranslationService(
                config,
                translation_client=SuspiciousSemanticClient(),
                summary_client=SuspiciousSemanticClient(),
                review_port=review_port,
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
            self.assertEqual(review_port.calls, 1)
            self.assertEqual(review_port.reviewed_translations, ["短。"])

    def test_tui_repair_uses_semantic_context_but_outputs_selected_timed_cue(self):
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:01,000", "A few months ago,", "旧译文一"),
            SubtitleEntry(2, "00:00:01,000", "00:00:02,000", "I wrote a few sentences", "旧译文二", True),
            SubtitleEntry(3, "00:00:02,000", "00:00:03,000", "that mattered.", "旧译文三"),
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
            QualityGate().diagnose_chunk(entries, target_language="Chinese"),
            cast(Any, translator),
            context="",
            target_language="Chinese",
            report=report,
            refine_translation=False,
        )

        self.assertTrue(outcome.retranslated)
        self.assertEqual(translator.repair_output_indices, [2])
        self.assertIn("A few months ago, I wrote a few sentences that mattered.", translator.repair_briefs[0])
        self.assertIn("Current translation: 旧译文二", translator.repair_briefs[0])
        self.assertEqual(entries[0].translated_text, "旧译文一")
        self.assertEqual(entries[1].translated_text, "修复译文1")
        self.assertEqual(entries[2].translated_text, "旧译文三")

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
            QualityGate().diagnose_chunk(entries, target_language="Chinese"),
            cast(Any, translator),
            context="",
            target_language="Chinese",
            report=report,
            refine_translation=False,
        )

        self.assertTrue(outcome.retranslated)
        self.assertEqual(translator.repair_output_indices, [2, 3])
        self.assertIn("A few months ago, I wrote a few sentences that mattered.", translator.repair_briefs[0])
        self.assertEqual(entries[0].translated_text, "旧译文一")
        self.assertEqual(entries[1].translated_text, "修复译文1")
        self.assertEqual(entries[2].translated_text, "修复译文2")

    def test_tui_semantic_repair_splits_large_ranges_into_batches(self):
        entries = [
            SubtitleEntry(index, "00:00:00,000", "00:00:01,000", f"source {index}", f"旧译文{index}", True)
            for index in range(1, 41)
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
            entries_to_retranslate=entries,
            cascade_start_index=1,
        )

        outcome = service._apply_semantic_tui_review_result(
            planned,
            entries,
            units,
            {entry.index: unit for unit in units for entry in unit.entries},
            review_result,
            QualityGate().diagnose_chunk(entries, target_language="Chinese"),
            cast(Any, translator),
            context="",
            target_language="Chinese",
            report=report,
            refine_translation=False,
        )

        self.assertTrue(outcome.retranslated)
        self.assertEqual([len(batch) for batch in translator.repair_output_batches], [8, 8, 8, 8, 8])
        self.assertEqual(entries[0].translated_text, "修复译文1")
        self.assertEqual(entries[8].translated_text, "修复译文1")
        self.assertEqual(entries[-1].translated_text, "修复译文8")

    def test_tui_semantic_repair_falls_back_to_smaller_batches_after_failure(self):
        entries = [
            SubtitleEntry(index, "00:00:00,000", "00:00:01,000", f"source {index}", f"旧译文{index}", True)
            for index in range(1, 6)
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
        translator = FailLargeRepairSemanticTranslator()
        service = TranslationService(
            make_config(),
            translation_client=SemanticTranslationClient(),
            summary_client=SemanticTranslationClient(),
        )
        review_result = ReviewResult(
            chunk=entries,
            entries_to_retranslate=entries,
            cascade_start_index=1,
        )

        outcome = service._apply_semantic_tui_review_result(
            planned,
            entries,
            units,
            {entry.index: unit for unit in units for entry in unit.entries},
            review_result,
            QualityGate().diagnose_chunk(entries, target_language="Chinese"),
            cast(Any, translator),
            context="",
            target_language="Chinese",
            report=report,
            refine_translation=False,
        )

        self.assertTrue(outcome.retranslated)
        self.assertEqual(translator.repair_output_batches, [[1, 2, 3, 4, 5], [1, 2], [3, 4], [5]])
        self.assertEqual([entry.translated_text for entry in entries], [
            "修复译文1",
            "修复译文2",
            "修复译文1",
            "修复译文2",
            "修复译文1",
        ])

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
            QualityGate().diagnose_chunk(entries, target_language="Chinese"),
            cast(Any, translator),
            context="",
            target_language="Chinese",
            report=report,
            refine_translation=False,
        )

        self.assertTrue(outcome.retranslated)
        self.assertEqual(translator.recorded_source_texts, [])
        self.assertEqual(translator.repair_stages, ["tui-semantic-drift-repair"])
        self.assertEqual(translator.drift_anchor_texts, ["A few months ago,"])
        self.assertEqual(translator.drift_source_texts, ["I wrote a few sentences", "that mattered."])
        self.assertEqual(entries[0].translated_text, "旧译文一")
        self.assertEqual(entries[1].translated_text, "修复译文1")
        self.assertEqual(entries[2].translated_text, "修复译文2")

    def test_tui_semantic_repair_failure_keeps_current_translations_for_next_review(self):
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
        translator = FailingRepairSemanticTranslator()
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
            QualityGate().diagnose_chunk(entries, target_language="Chinese"),
            cast(Any, translator),
            context="",
            target_language="Chinese",
            report=report,
            refine_translation=False,
        )

        self.assertFalse(outcome.retranslated)
        self.assertTrue(outcome.did_attempt)
        self.assertEqual([entry.translated_text for entry in entries], ["旧译文一", "旧译文二", "旧译文三"])
        self.assertFalse(entries[0].needs_retranslation)
        self.assertTrue(entries[1].needs_retranslation)
        self.assertTrue(entries[2].needs_retranslation)

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
