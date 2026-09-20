"""语义复核的风险路由、缓存失效、服务失败和真实流水线接线回归。"""

import contextlib
import io
import copy
from concurrent.futures import ThreadPoolExecutor
import threading
from pathlib import Path
import tempfile
import unittest
from typing import Literal
from unittest.mock import patch

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.io import SubtitleIO
from subtitle_llm.llm.types import CompletionResult, CompletionUsage
from subtitle_llm.pipeline import TranslationRequest, TranslationService
from subtitle_llm.pipeline.chunks import PlannedChunk
from subtitle_llm.pipeline.model_segmentation import SegmentationSource
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.review_policy import ReviewPolicy
from subtitle_llm.pipeline.semantic_quality import JevSemanticReviewer, SemanticQualityGate, build_request, validate_answers
from subtitle_llm.pipeline.task_store import TranslationTaskStore
from subtitle_llm.settings import AppConfig, ModelConfig, ModelProvider, PipelineConfig


def cue(index=10, translation="我认为这会奏效。"):
    return SubtitleEntry(index, "00:00:00,000", "00:00:04,000", "I don't think this will work.", translation)


def report():
    return TranslationReport(input_file="input.srt", output_file="output.srt", context_file="context.txt")


class FakeEvaluator:
    def __init__(self):
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)
        answers = {}
        bad = next((line for line, cue in payload["state"]["cues"].items()
                    if cue["translation"] == "我认为这会奏效。"), None)
        for name, question in payload["questions"].items():
            if question["type"] == "boolean":
                answers[name] = {"type": "boolean", "probability": 0.97 if name == "meaning_exists" and bad else 0.02}
            else:
                candidates = question["criteria"]
                choice = bad if bad in candidates else next(iter(candidates))
                answers[name] = {"type": "choice", "choice": choice,
                                 "probabilities": {line: float(line == choice) for line in candidates}}
        return {"answers": answers, "usage": {"inputTokens": 100, "outputTokens": 10, "totalTokens": 110}}


class TestSemanticQuality(unittest.TestCase):
    def setUp(self):
        self.report = report()
        self.evaluate = FakeEvaluator()
        self.reviewer = JevSemanticReviewer(context="", report=self.report, evaluate=self.evaluate)
        self.gate = SemanticQualityGate(self.reviewer)

    def test_semantic_error_flags_local_position_without_auto_repair(self):
        entries = [cue(42)]
        diagnosis = self.gate.diagnose_chunk(entries, target_language="Chinese")
        self.gate.apply_diagnosis(entries, diagnosis)
        self.assertTrue(entries[0].needs_retranslation)
        self.assertEqual(diagnosis.issues[0].index, 1)
        self.assertFalse(ReviewPolicy("auto").should_auto_repair(diagnosis))
        self.assertFalse(ReviewPolicy("auto").quality_visual_auto_repair(diagnosis))
        self.assertTrue(ReviewPolicy("tui").should_manual_review(diagnosis))

    def test_identical_evidence_reuses_cache_changed_translation_rechecks(self):
        entries = [cue()]
        self.gate.diagnose_chunk(entries, target_language="Chinese")
        self.gate.diagnose_chunk(entries, target_language="Chinese")
        self.assertEqual(len(self.evaluate.calls), 1)
        entries[0].translated_text = "我觉得这行不通。"
        diagnosis = self.gate.diagnose_chunk(entries, target_language="Chinese")
        self.assertFalse(diagnosis.has_issues)
        self.assertEqual(len(self.evaluate.calls), 2)
        self.assertEqual(self.report.token_usage.total_tokens, 220)
        self.reviewer.context = "New evidence"
        self.gate.diagnose_chunk(entries, target_language="Chinese")
        self.assertEqual(len(self.evaluate.calls), 3)

    def test_uncertainty_is_review_only(self):
        def uncertain(payload):
            result = FakeEvaluator()(payload)
            result["answers"]["meaning_exists"]["probability"] = 0.5
            return result
        gate = SemanticQualityGate(JevSemanticReviewer(context="", report=report(), evaluate=uncertain))
        diagnosis = gate.diagnose_chunk([cue()], target_language="Chinese")
        self.assertIn("不确定", diagnosis.issues[0].description)
        self.assertFalse(ReviewPolicy("auto").should_auto_repair(diagnosis))

    def test_missing_translation_still_triggers_rule_repair_without_api(self):
        diagnosis = self.gate.diagnose_chunk([cue(translation="")], target_language="Chinese")
        self.assertTrue(ReviewPolicy("auto").should_auto_repair(diagnosis))
        self.assertFalse(self.evaluate.calls)

    def test_service_failure_is_warning_and_does_not_loop(self):
        calls = []
        def fail(payload):
            calls.append(payload)
            raise RuntimeError("private error details")
        gate = SemanticQualityGate(JevSemanticReviewer(context="", report=self.report, evaluate=fail))
        for translation in ["我认为这会奏效。", "我觉得这行不通。"]:
            diagnosis = gate.diagnose_chunk([cue(translation=translation)], target_language="Chinese")
            self.assertTrue(diagnosis.has_issues)
            self.assertIn("semantic_unavailable", {issue.issue_type for issue in diagnosis.issues})
            self.assertFalse(ReviewPolicy("auto").should_auto_repair(diagnosis))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("private error", self.report.model_dump_json())

    def test_incomplete_or_invalid_answers_are_rejected(self):
        request = build_request([cue()], "Chinese", "")
        for value in [float("nan"), -1, 1.1, True, "0.9"]:
            result = self.evaluate(request)
            result["answers"]["meaning_exists"]["probability"] = value
            with self.assertRaises(ValueError):
                validate_answers(request, result)
        with self.assertRaises(ValueError):
            validate_answers(request, {"answers": {}})

    def test_oversized_evidence_is_not_silently_truncated(self):
        entries = [cue(translation="我觉得这行不通。")]
        entries[0].original_text = "长" * 32_000
        diagnosis = self.gate.diagnose_chunk(entries, target_language="Chinese")
        self.assertIn("semantic_unavailable", {issue.issue_type for issue in diagnosis.issues})
        self.assertEqual(self.report.semantic_quality_checks[0]["reason"], "context_exceeded")
        self.assertFalse(self.evaluate.calls)

    def test_large_chunk_uses_eight_questions_and_preserves_all_evidence(self):
        entries = [cue(i) for i in range(116)]
        self.reviewer.check(entries, "Chinese")
        self.assertEqual(len(self.evaluate.calls), 1)
        payload = self.evaluate.calls[0]
        self.assertEqual(len(payload["state"]["cues"]), 116)
        self.assertEqual(len(payload["questions"]), 8)
        choices = [q for q in payload["questions"].values() if q["type"] == "choice"]
        self.assertEqual(len(choices), 4)
        self.assertTrue(all(set(q["criteria"]) == {str(i + 1) for i in range(116)} for q in choices))

    def test_distant_evidence_change_invalidates_chunk_judgment(self):
        entries = [cue(i) for i in range(15)]
        self.reviewer.check(entries, "Chinese")
        self.reviewer.check(entries, "Chinese")
        self.assertEqual(len(self.evaluate.calls), 1)
        entries[-1].original_text = "The speaker is referring to a different person."
        self.reviewer.check(entries, "Chinese")
        self.assertEqual(len(self.evaluate.calls), 2)
        self.assertEqual(self.evaluate.calls[-1]["state"]["cues"]["15"]["source"], entries[-1].original_text)

    def test_ranking_alone_never_flags_clean_chunk(self):
        entries = [cue(42, "我觉得这行不通。"), cue(99, "我觉得这行不通。")]
        diagnosis = self.gate.diagnose_chunk(entries, target_language="Chinese")
        self.assertFalse(diagnosis.has_issues)
        self.assertEqual(self.report.semantic_quality_checks[0]["judgments"]["meaning"]["line_probabilities"]["1"], 1)

    def test_flat_ranking_uses_candidates_not_independent_error_thresholds(self):
        def ranked(payload):
            result = FakeEvaluator()(payload)
            result["answers"]["meaning_where"] = {
                "type": "choice", "choice": "8", "probabilities": {str(i + 1): .1 for i in range(10)},
            }
            return result
        reviewer = JevSemanticReviewer(context="", report=report(), evaluate=ranked)
        issues = reviewer.check([cue(i * 10) for i in range(10)], "Chinese")
        self.assertEqual([x.index for x in issues], [1])
        self.assertEqual(issues[0].related_indices, list(range(2, 11)))
        self.assertEqual(issues[0].metrics["chunk_probability"], .97)
        self.assertEqual(issues[0].metrics["location_distribution"]["8"], .1)
        self.assertNotIn("probability", issues[0].metrics)
        self.assertIn("不是该行错误概率", issues[0].description)
        self.assertTrue(all(x.severity == "low" for x in issues))

    def test_missing_translation_excluded_from_choices_without_renumbering(self):
        entries = [cue(42, ""), cue(999)]
        issues = self.reviewer.check(entries, "Chinese")
        payload = self.evaluate.calls[0]
        self.assertEqual(set(payload["state"]["cues"]), {"1", "2"})
        self.assertEqual(payload["questions"]["meaning_where"]["criteria"], {"2": None})
        self.assertEqual([i.index for i in issues], [2])

    def test_choice_protocol_rejects_unknown_missing_or_invalid_scores(self):
        payload = build_request([cue(42), cue(99)], "Chinese", "")
        valid = FakeEvaluator()(payload)
        bad_where = [
            {"type": "choice", "choice": "999", "probabilities": {"1": .5, "2": .5}},
            {"type": "choice", "choice": "1", "probabilities": {"1": 1}},
            {"type": "choice", "choice": "1", "probabilities": {"1": .2, "2": .2}},
            {"type": "choice", "choice": "1", "probabilities": {"1": .1, "2": .9}},
            {"type": "choice", "choice": "1", "probabilities": {"1": float("nan"), "2": .2}},
            {"type": "choice", "choice": "1", "probabilities": {"1": True, "2": 0}},
        ]
        for where in bad_where:
            with self.subTest(where=where):
                result = copy.deepcopy(valid)
                result["answers"]["meaning_where"] = where
                with self.assertRaises(ValueError):
                    validate_answers(payload, result)

    def test_distribution_rounding_uses_provider_metadata(self):
        payload = build_request([cue(i) for i in range(3)], "Chinese", "")
        result = FakeEvaluator()(payload)
        result["answers"]["meaning_where"]["probabilities"] = {str(i + 1): .33 for i in range(3)}
        with self.assertRaises(ValueError):
            validate_answers(payload, result)
        result["rounding"] = {"probabilityDecimals": 2}
        self.assertEqual(validate_answers(payload, result)["meaning"]["line_probabilities"]["1"], .33)

    def test_choice_limit_never_silently_drops_lines(self):
        issues = self.reviewer.check([cue(i) for i in range(256)], "Chinese")
        self.assertFalse(self.evaluate.calls)
        self.assertEqual(len(issues), 256)
        self.assertEqual(self.report.semantic_quality_checks[0]["reason"], "candidate_limit_exceeded")
        self.reviewer.check([cue()], "Chinese")
        self.assertEqual(len(self.evaluate.calls), 1)

    def test_completed_cache_does_not_wait_for_unrelated_network_request(self):
        started, release = threading.Event(), threading.Event()
        evaluator = FakeEvaluator()
        def evaluate(payload):
            if payload["state"]["cues"]["1"]["source"] == "slow request":
                started.set()
                if not release.wait(5):
                    raise AssertionError("test did not release network request")
            return evaluator(payload)
        reviewer = JevSemanticReviewer(context="", report=report(), evaluate=evaluate)
        cached = [cue()]
        reviewer.check(cached, "Chinese")
        slow = [cue(99)]
        slow[0].original_text = "slow request"
        with ThreadPoolExecutor(max_workers=2) as pool:
            network = pool.submit(reviewer.check, slow, "Chinese")
            try:
                self.assertTrue(started.wait(2))
                result = pool.submit(reviewer.check, cached, "Chinese").result(timeout=2)
                self.assertEqual(result[0].index, 1)
            finally:
                release.set()
            network.result(timeout=2)
        self.assertEqual(len(evaluator.calls), 2)

    def test_failed_evidence_is_reported_once(self):
        def fail(_):
            raise RuntimeError("private error details")
        reviewer = JevSemanticReviewer(context="", report=self.report, evaluate=fail)
        for _ in range(5):
            reviewer.check([cue()], "Chinese")
        self.assertEqual(len(self.report.semantic_quality_checks), 1)

    def test_translation_boundaries_and_source_provenance_reach_review(self):
        entries = [cue(i) for i in range(3)]
        source = SegmentationSource(entries)
        planned = PlannedChunk(2, [entries[1]], "readonly neighboring dialogue", [])
        reviewer = JevSemanticReviewer(
            context="scene and terminology", report=report(), evaluate=self.evaluate,
            source_kind="asr", source_language="English",
            segmentation_context={planned.index: source.readonly_context(*source.bounds(planned.entries))},
        )
        reviewer.check([entries[1]], "Chinese", planned=planned)
        state = self.evaluate.calls[-1]["state"]
        scope = state["translation_context"]
        self.assertEqual(scope["source_kind"], "asr")
        self.assertEqual(scope["chunk_number"], 3)
        self.assertEqual(scope["readonly_boundary_context"], planned.boundary_context)
        self.assertEqual((scope["readonly_before"], scope["readonly_after"]), source.readonly_context(*source.bounds(planned.entries)))
        self.assertTrue(scope["readonly_before"] and scope["readonly_after"])
        self.assertEqual(state["generated_context"], "scene and terminology")
        planned.boundary_context = "changed neighboring dialogue"
        # 模拟模型断句后源编号变成新 cue 编号，边界证据必须仍取原始翻译范围。
        planned.entries = [cue(9999)]
        reviewer.check([entries[1]], "Chinese", planned=planned)
        self.assertEqual(len(self.evaluate.calls), 2)
        latest = self.evaluate.calls[-1]["state"]["translation_context"]
        self.assertEqual(latest["readonly_before"], scope["readonly_before"])
        self.assertEqual(latest["readonly_after"], scope["readonly_after"])

    def test_full_context_overflow_never_drops_distant_cues(self):
        entries = [cue(i) for i in range(10)]
        entries[-1].original_text = "长" * 32_000
        issues = self.reviewer.check(entries, "Chinese")
        self.assertEqual({i.index for i in issues}, set(range(1, 11)))
        self.assertFalse(self.evaluate.calls)
        self.assertTrue(all(r["reason"] == "context_exceeded" for r in self.report.semantic_quality_checks))
        # 超预算不会把下一片段的服务状态熔断。
        self.reviewer.check([cue()], "Chinese")
        self.assertEqual(len(self.evaluate.calls), 1)


class FixedClient:
    def __init__(self, response):
        self.response, self.calls = response, 0

    def create_completion(self, config, messages):
        self.calls += 1
        return CompletionResult(self.response, CompletionUsage())


def run_fixture(root, evaluate, *, segmentation: Literal["off", "always"] = "off", review_port=None):
    source = root / "input.srt"
    SubtitleIO.write_srt(Subtitle([cue(1, "")]), source, output_format="source-only")
    model = ModelConfig(type=ModelProvider.CUSTOM, model="fixture", endpoint="https://fixture.invalid", api_key_env="FIXTURE_KEY")
    config = AppConfig(summary_model=model, translation_model=model, pipeline=PipelineConfig(
        semantic_quality="jev", model_segmentation=segmentation, semantic_translation="off",
        normalize_subtitles="off", ignore_subtitle_length=0, fallback_on_chunk_error="abort",
    ))
    translation = '[{"end":6,"text":"我认为这会奏效。"}]' if segmentation == "always" else "[1]\n我认为这会奏效。"
    client = FixedClient(translation)
    summary = FixedClient('{"summary":"A statement.","terms":[],"source_corrections":[]}')
    store = TranslationTaskStore(root / "tasks.sqlite3")
    with patch("subtitle_llm.pipeline.semantic_quality.gateway_evaluate", evaluate), contextlib.redirect_stdout(io.StringIO()):
        result = TranslationService(config, translation_client=client, summary_client=summary,
                                    task_store=store, review_port=review_port).translate(
            TranslationRequest(str(source), str(root / "output.srt"), "Chinese")
        )
    return result, store, client


class TestSemanticQualityPipeline(unittest.TestCase):
    def test_standard_and_model_segmented_paths_warn_without_retranslation(self):
        modes: list[Literal["off", "always"]] = ["off", "always"]
        for segmentation in modes:
            with self.subTest(segmentation=segmentation), tempfile.TemporaryDirectory() as temp:
                result, store, client = run_fixture(Path(temp), FakeEvaluator(), segmentation=segmentation)
                self.assertEqual(client.calls, 1)
                self.assertFalse(result.report.failed_chunks)
                self.assertTrue(result.subtitle.entries[0].needs_retranslation)
                assert result.report.task_id is not None
                self.assertEqual(store.get_task(result.report.task_id).status, "completed_with_warnings")
                self.assertEqual(result.report.semantic_quality_checks[0]["status"], "checked")
                semantic = store.load_model_result(result.report.task_id, result.report.semantic_quality_checks[0]["request_key"])
                self.assertIsNotNone(semantic)
                assert semantic is not None
                self.assertIn("request", semantic)
                # 新 reviewer 重用 SQLite 判断，不再收费。
                state = semantic["request"]["state"]
                self.assertEqual(state["translation_context"]["chunk_number"], 1)
                self.assertEqual(state["translation_context"]["source_kind"], "subtitle")
                replay_report = report()
                replay_report.total_chunks = state["translation_context"]["total_chunks"]
                reviewer = JevSemanticReviewer(
                    context="", report=replay_report, store=store, task_id=result.report.task_id,
                    evaluate=lambda _: self.fail("unexpected request"), source_kind="subtitle",
                    source_language=state["translation_context"]["source_language"],
                    segmentation_context={0: ("", "")} if segmentation == "always" else None,
                )
                reviewer.context = semantic["request"]["state"]["generated_context"]
                reviewer.check(result.subtitle.entries, "Chinese", planned=PlannedChunk(
                    0, [cue(1, "")], state["translation_context"]["readonly_boundary_context"], [],
                ))
                self.assertTrue(reviewer.report.semantic_quality_checks[0]["cached"])


if __name__ == "__main__":
    unittest.main()
