import contextlib
import io
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.io.subtitles import SubtitleIO
from subtitle_llm.llm.types import CompletionResult, CompletionUsage
from subtitle_llm.pipeline.chunk_translator import ChunkTranslator
from subtitle_llm.pipeline.chunks import PlannedChunk
from subtitle_llm.pipeline.llm_trace import LlmTraceRecorder
from subtitle_llm.pipeline.model_segmentation import (
    Piece, SegmentationSource, compact_context, context_without_cue_ids, missing_ranges, parse_pieces, segmentation_prompt,
)
from subtitle_llm.pipeline.model_segmenter import ModelSegmenter
from subtitle_llm.pipeline.normalization import normalize_text, srt_time_to_ms
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.service import ResolvedInput, TranslationRequest, TranslationService
from subtitle_llm.pipeline.task_store import TranslationTaskStore
from subtitle_llm.settings import AppConfig, ModelConfig, ModelProvider, PipelineConfig


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.prompts = []

    def create_completion(self, config, messages):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(prompt)
        if isinstance(response, CompletionResult):
            return response
        assert isinstance(response, str)
        return CompletionResult(response, CompletionUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120))


def config(**options):
    model = ModelConfig(type=ModelProvider.CUSTOM, model="fake", endpoint="https://fake.test", api_key_env="FAKE_KEY")
    return AppConfig(summary_model=model, translation_model=model,
                     pipeline=PipelineConfig(model_segmentation="always", chunk_size=2, threads=2,
                                             fallback_on_chunk_error="abort", **options))


def entry(index, text, start=0, end=6000):
    from subtitle_llm.pipeline.normalization import ms_to_srt_time
    return SubtitleEntry(index, ms_to_srt_time(start), ms_to_srt_time(end), text)


class TestSegmentationProtocol(unittest.TestCase):
    def test_model_boundary_preserves_phrase_and_original_time_window(self):
        text = "If you're an SRE, a systems administrator, or a DevOps engineer, this course is for you."
        source = SegmentationSource([entry(1, text)])
        pieces = parse_pieces('[{"end":7,"text":"如果你是SRE或系统管理员，"},'
                              '{"end":16,"text":"或DevOps工程师，这门课适合你。"}]', 1, 16)
        self.assertEqual(missing_ranges(pieces, 1, 16), [])
        cues = source.to_cues(pieces, PipelineConfig())
        self.assertEqual(normalize_text(" ".join(c.original_text for c in cues)), text)
        self.assertTrue(normalize_text(cues[-1].original_text).endswith("this course is for you."))
        self.assertEqual(cues[-1].end_time, "00:00:06,000")
        self.assertLessEqual(srt_time_to_ms(cues[0].end_time), srt_time_to_ms(cues[1].start_time))

    def test_short_independent_utterance_is_not_extended(self):
        source = SegmentationSource([entry(1, "Yes.", 0, 350), entry(2, "Continue.", 400, 1000)])
        cues = source.to_cues([Piece(1, 1, "是的。"), Piece(2, 2, "继续。")], PipelineConfig())
        self.assertEqual([(c.start_time, c.end_time) for c in cues],
                         [("00:00:00,000", "00:00:00,350"), ("00:00:00,400", "00:00:01,000")])

    def test_cjk_positions_reconstruct_without_invented_spaces(self):
        source = SegmentationSource([entry(1, "加载模型到GPU显存。")])
        end = len(source.positions)
        cues = source.to_cues([Piece(1, end, "Load the model into GPU memory.")], PipelineConfig())
        self.assertEqual(cues[0].original_text, "加载模型到GPU显存。")

    def test_empty_middle_translation_preserves_both_neighbors(self):
        pieces = parse_pieces('[{"end":2,"text":"左"},{"end":4,"text":""},{"end":6,"text":"右"}]', 1, 6)
        self.assertEqual(pieces, [Piece(1, 2, "左"), Piece(5, 6, "右")])
        self.assertEqual(missing_ranges(pieces, 1, 6), [(3, 4)])

    def test_bad_boundary_also_invalidates_following_start(self):
        pieces = parse_pieces('[{"end":2,"text":"左"},{"end":"?","text":"错"},'
                              '{"end":5,"text":"不可信"},{"end":6,"text":"右"}]', 1, 6)
        self.assertEqual(missing_ranges(pieces, 1, 6), [(3, 5)])
        self.assertEqual(pieces[-1], Piece(6, 6, "右"))

    def test_truncated_response_salvages_only_complete_objects(self):
        pieces = parse_pieces('[{"end":2,"text":"已完成"},{"end":4,"text":"未完', 1, 6)
        self.assertEqual(pieces, [Piece(1, 2, "已完成")])
        self.assertEqual(missing_ranges(pieces, 1, 6), [(3, 6)])

    def test_invalid_positions_and_punctuation_do_not_count_as_coverage(self):
        for value in (True, 0, 99, 1.5):
            with self.subTest(value=value):
                pieces = parse_pieces(json.dumps([{"end":value,"text":"错"}]), 1, 6)
                self.assertEqual(missing_ranges(pieces, 1, 6), [(1, 6)])
        self.assertEqual(parse_pieces('[{"end":6,"text":"..."}]', 1, 6), [])

    def test_prompt_has_one_source_copy_and_only_relevant_context(self):
        source = SegmentationSource([entry(1, "Load the GPU memory.")])
        context = ('Overall summary: A hardware tutorial.\nShort Terms: GPU(显卡), CPU(处理器)\n'
                   'Source corrections JSON: []\nLikely ASR corrections: a duplicate')
        prompt = segmentation_prompt(source, 1, 4, context, "Chinese", PipelineConfig())
        self.assertEqual(prompt.count("1:Load 2:the 3:GPU 4:memory."), 1)
        self.assertNotIn("CPU", prompt)
        self.assertNotIn("a duplicate", prompt)
        self.assertNotIn("00:00:", prompt)
        self.assertEqual(compact_context("Custom instructions", "source"), "Custom instructions")

    def test_old_asr_cue_ids_cannot_target_unrelated_generated_cues(self):
        from subtitle_llm.pipeline.source_corrections import find_unadopted_hard_corrections, source_corrections_from_context
        context = 'Source corrections JSON: ' + json.dumps([{
            "cue_ids": [1], "observed": "code cloud", "corrected": "KodeKloud", "type": "organization",
            "enforcement": "hard", "target_aliases": ["KodeKloud"], "confidence": "high",
        }]) + '\nLikely ASR corrections: code cloud -> KodeKloud'
        corrections = source_corrections_from_context(context_without_cue_ids(context))
        self.assertEqual(corrections[0].cue_ids, ())
        self.assertFalse(find_unadopted_hard_corrections(corrections, [entry(1, "An unrelated sentence.")]))


class TestModelSegmenter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = TranslationTaskStore(Path(self.tmp.name) / "tasks.db")
        self.config = config()
        self.record = self.store.create_task(
            input_display="input.srt", working_directory=self.tmp.name, source_subtitle_path="input.srt",
            normalized_input_fingerprint="source", target_language="Chinese", source_language="en",
            output_format="source-first", output_file="output.srt", config=self.config,
        )
        self.source = SegmentationSource([entry(1, "one two three four five six")])
        self.planned = PlannedChunk(0, [entry(1, "one two three four five six")], "", [])
        self.report = TranslationReport(input_file="input.srt", output_file="output.srt", context_file="context.txt")

    def segmenter(self, client):
        return ModelSegmenter(self.source, ChunkTranslator(client, self.config.translation_model),
                              self.config.pipeline, "context", "Chinese", self.store, self.record.task_id, self.report)

    def test_repairs_only_missing_range_and_reuses_accepted_output(self):
        client = FakeClient(['[{"end":2,"text":"一二"},{"end":4,"text":""},{"end":6,"text":"五六"}]',
                             '[{"end":2,"text":"三四"}]'])
        segmenter = self.segmenter(client)
        generated = segmenter.translate(self.planned)
        self.assertEqual([e.translated_text for e in generated.result.chunk], ["一二", "三四", "五六"])
        self.assertIn("Source positions 1-2:", client.prompts[1])
        self.assertIn("1:three 2:four", client.prompts[1])
        self.assertNotIn("1:one", client.prompts[1])
        self.assertEqual(self.report.token_usage.total_tokens, 240)
        # 模拟复核合并前两条；缓存必须保存实际接受结果，而不只是模型原始结果。
        kept = generated.result.chunk
        kept[0].original_text += " " + kept[1].original_text
        kept[0].translated_text += kept[1].translated_text
        kept[0].end_time = kept[1].end_time
        kept = [kept[0], kept[2]]
        segmenter.accept(generated, kept)
        resumed = self.segmenter(FakeClient([])).translate(self.planned)
        self.assertTrue(resumed.accepted)
        self.assertEqual(len(resumed.result.chunk), 2)
        self.assertEqual(resumed.result.chunk[0].translated_text, "一二三四")

    def test_interrupted_repair_resumes_without_repeating_initial_request(self):
        client = FakeClient(['[{"end":2,"text":"一二"}]', RuntimeError("connection lost")])
        with self.assertRaisesRegex(RuntimeError, "connection lost"):
            self.segmenter(client).translate(self.planned)
        resumed = FakeClient(['[{"end":4,"text":"三四五六"}]'])
        result = self.segmenter(resumed).translate(self.planned)
        self.assertEqual(len(resumed.prompts), 1)
        self.assertIn("Source positions 1-4:", resumed.prompts[0])
        self.assertEqual(result.result.chunk[0].translated_text, "一二")

    def test_empty_budget_exhaustion_does_not_repeat_full_request(self):
        client = FakeClient([CompletionResult("", CompletionUsage(total_tokens=500), "length")])
        with self.assertRaisesRegex(ValueError, "没有可恢复输出"):
            self.segmenter(client).translate(self.planned)
        self.assertEqual(len(client.prompts), 1)
        self.assertEqual(self.report.token_usage.total_tokens, 500)

    def test_changed_context_invalidates_cached_decisions(self):
        client = FakeClient(['[{"end":6,"text":"全部"}]', '[{"end":6,"text":"新译文"}]'])
        segmenter = self.segmenter(client)
        generated = segmenter.translate(self.planned)
        segmenter.accept(generated, generated.result.chunk)
        segmenter.context = "new context"
        self.assertFalse(segmenter.translate(self.planned).accepted)
        self.assertEqual(len(client.prompts), 2)

    def test_local_chunk_and_repair_positions_map_back_to_global_timing(self):
        earlier = entry(1, "Earlier. " * 1200, 0, 10000)
        current = entry(2, "seven eight nine ten", 11000, 15000)
        self.source = SegmentationSource([earlier, current])
        self.planned = PlannedChunk(1, [current], "", [])
        client = FakeClient(['[{"end":2,"text":"七八"},{"end":4,"text":""}]',
                             '[{"end":2,"text":"九十"}]'])
        segmenter = self.segmenter(client)
        segmenter.translator.trace_recorder = LlmTraceRecorder(Path(self.tmp.name) / "trace")
        generated = segmenter.translate(self.planned)
        self.assertEqual(generated.pieces, [Piece(1201, 1202, "七八"), Piece(1203, 1204, "九十")])
        self.assertEqual([cue.index for cue in generated.result.chunk], [1201, 1203])
        self.assertEqual(generated.result.chunk[0].start_time, "00:00:11,000")
        self.assertEqual(generated.result.chunk[-1].end_time, "00:00:15,000")
        self.assertIn("1:seven 2:eight 3:nine 4:ten", client.prompts[0])
        self.assertIn("1:nine 2:ten", client.prompts[1])
        traces = [json.loads(p.read_text()) for p in sorted((Path(self.tmp.name) / "trace").glob("*.json"))]
        self.assertEqual([t["status"] for t in traces], ["suspicious", "ok"])
        self.assertTrue(all(t["parse"] is None for t in traces))
        self.assertEqual(traces[0]["source_coverage"]["missing_ranges"], [[1203, 1204]])
        self.assertEqual(traces[1]["source_coverage"]["response_position_offset"], 1202)
        segmenter.accept(generated, generated.result.chunk)
        self.assertTrue(self.segmenter(FakeClient([])).translate(self.planned).accepted)

    def test_v1_accepted_cache_survives_protocol_upgrade_without_calls(self):
        # v1 固定夹具键：同 setUp 的原文/配置/context；不能随 v2 提示一起重算。
        key = "1737b43821f2948d5883150e5566efa154cdb2e071eb99da5caa7345882a7996"
        pieces = [Piece(1, 2, "一二"), Piece(3, 6, "三四五六")]
        accepted = self.source.to_cues([Piece(1, 6, "人工接受的译文")], self.config.pipeline)
        self.store.save_model_result(self.record.task_id, key, {
            "pieces": [p.to_dict() for p in pieces],
            "accepted_entries": [e.to_dict() for e in accepted], "removed_indices": [3],
        })
        generated = self.segmenter(FakeClient([])).translate(self.planned)
        self.assertTrue(generated.accepted)
        self.assertEqual(generated.result.chunk[0].translated_text, "人工接受的译文")
        self.assertEqual(generated.removed_indices, [3])
        self.assertEqual(self.report.token_usage.total_tokens, 0)

    def test_v1_partial_cache_only_completes_missing_range_using_local_positions(self):
        key = "1737b43821f2948d5883150e5566efa154cdb2e071eb99da5caa7345882a7996"
        self.store.save_model_result(self.record.task_id, key, {"pieces": [Piece(1, 2, "一二").to_dict()]})
        client = FakeClient(['[{"end":4,"text":"三四五六"}]'])
        generated = self.segmenter(client).translate(self.planned)
        self.assertEqual(generated.pieces, [Piece(1, 2, "一二"), Piece(3, 6, "三四五六")])
        self.assertEqual(len(client.prompts), 1)
        self.assertIn("1:three 2:four 3:five 4:six", client.prompts[0])

    def test_number_in_neighbor_gets_one_local_model_boundary_repair(self):
        self.source = SegmentationSource([entry(1, "Earlier. Later."),
                                          entry(2, "Recreate Orbit 2 inside this engine.", 6000, 10000)])
        self.planned = PlannedChunk(1, [entry(2, "Recreate Orbit 2 inside this engine.", 6000, 10000)], "", [])
        client = FakeClient(['[{"end":3,"text":"在这个引擎中，"},{"end":6,"text":"复刻Orbit 2。"}]',
                             '[{"end":6,"text":"在这个引擎中复刻Orbit 2。"}]'])
        segmenter = self.segmenter(client)
        generated = segmenter.translate(self.planned)
        self.assertEqual(generated.pieces, [Piece(3, 8, "在这个引擎中复刻Orbit 2。")])
        self.assertEqual(len(client.prompts), 2)
        self.assertIn("previous attempt moved", client.prompts[1])
        self.assertIn("1:Recreate", client.prompts[1])
        # 只生成、还没进入 TUI 时中断，也应复用已完成的修正。
        resumed = self.segmenter(FakeClient([])).translate(self.planned)
        self.assertEqual(resumed.pieces, generated.pieces)

    def test_bad_alignment_repair_keeps_original_and_does_not_repeat_on_resume(self):
        self.source = SegmentationSource([entry(1, "Recreate Orbit 2 inside this engine.")])
        self.planned = PlannedChunk(0, [entry(1, "Recreate Orbit 2 inside this engine.")], "", [])
        original = '[{"end":3,"text":"在这个引擎中，"},{"end":6,"text":"复刻Orbit 2。"}]'
        for repair in ('[{"end":6,"text":"复刻游戏。"}]', '[{"end":3,"text":"复刻Orbit 2。"}]', RuntimeError("offline")):
            with self.subTest(repair=repair):
                # 独立上下文提供独立请求键，不依赖清空已有缓存。
                segmenter = self.segmenter(FakeClient([original, repair]))
                segmenter.context = str(repair)
                generated = segmenter.translate(self.planned)
                self.assertEqual(generated.pieces, [Piece(1, 3, "在这个引擎中，"), Piece(4, 6, "复刻Orbit 2。")])
                segmenter.translator.client = FakeClient([])
                segmenter.translator.operations.client = FakeClient([])
                self.assertEqual(segmenter.translate(self.planned).pieces, generated.pieces)

    def test_alignment_does_not_exceed_shared_repair_budget(self):
        text = "Orbit 2 in engine. Then model 3 runs."
        self.source = SegmentationSource([entry(1, text)])
        self.planned = PlannedChunk(0, [entry(1, text)], "", [])
        client = FakeClient([
            '[{"end":2,"text":""},{"end":4,"text":"Orbit 2"},{"end":6,"text":""},{"end":8,"text":"模型3运行。"}]',
            '[{"end":2,"text":"在引擎中。"}]', '[{"end":2,"text":"然后模型，"}]',
        ])
        generated = self.segmenter(client).translate(self.planned)
        self.assertEqual(len(client.prompts), 3)
        self.assertEqual(missing_ranges(generated.pieces, 1, 8), [])
        self.assertFalse(any("previous attempt moved" in prompt for prompt in client.prompts))


class TestModelSegmentationPipeline(unittest.TestCase):
    def test_accept_all_keeps_quality_evidence_after_resume(self):
        from subtitle_llm.review.ports import ReviewResult

        class AcceptAllReview:
            calls = 0

            def review(self, chunk, chunk_index, total_chunks, completed_chunks=0):
                self.calls += 1
                return ReviewResult(chunk, [])

        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            input_path = root / "input.srt"
            SubtitleIO.write_srt(Subtitle([entry(1, "Version 10 is ready.")]), input_path, output_format="source-only")
            cfg = config(review_mode="tui")
            review = AcceptAllReview()
            store = TranslationTaskStore(root / "tasks.db")
            service = TranslationService(cfg, translation_client=FakeClient(['[{"end":4,"text":"版本就绪。"}]']),
                summary_client=FakeClient(['{"summary":"A release.","terms":[],"source_corrections":[]}']),
                review_port=review, task_store=store)
            result = service.translate(TranslationRequest(str(input_path), str(root / "out.srt"), "Chinese"))
            self.assertEqual(review.calls, 1)
            self.assertFalse(result.subtitle.entries[0].needs_retranslation)
            with contextlib.closing(sqlite3.connect(store.db_path)) as connection:
                before = connection.execute("SELECT status, diagnosis_json, last_trace_id FROM translation_chunks").fetchone()
            self.assertEqual(before[0], "accepted")
            diagnosis = json.loads(before[1])
            self.assertEqual(diagnosis["initial"]["flagged_entries"], 1)
            self.assertEqual(diagnosis["final"]["flagged_entries"], 1)
            self.assertEqual(diagnosis["review"], "manual")
            self.assertTrue(diagnosis["accepted_with_issues"])
            self.assertIsNotNone(before[2])
            TranslationService(cfg, translation_client=FakeClient([]), summary_client=FakeClient([]),
                               review_port=review, task_store=store).translate(
                TranslationRequest(None, None, "Chinese", resume=True, task_id=result.report.task_id))
            with contextlib.closing(sqlite3.connect(store.db_path)) as connection:
                after = connection.execute("SELECT status, diagnosis_json, last_trace_id FROM translation_chunks").fetchone()
            self.assertEqual(after, before)
            self.assertEqual(review.calls, 1)

    def test_tui_merge_survives_export_and_resume_without_more_model_calls(self):
        from subtitle_llm.review.ports import ReviewResult

        class MergeReview:
            def __init__(self):
                self.calls = 0

            def review(self, chunk, chunk_index, total_chunks, completed_chunks=0):
                self.calls += 1
                left, right = chunk
                left.original_text += " " + right.original_text
                left.translated_text = "版本10就绪，可以继续。"
                left.end_time = right.end_time
                return ReviewResult([left], [], removed_entry_indices=[right.index])

        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            input_path = root / "input.srt"
            SubtitleIO.write_srt(Subtitle([
                entry(1, "Version 10 is ready.", 0, 2500), entry(2, "Continue now.", 2600, 4000),
                entry(3, "Yes.", 4500, 5000),
            ]), input_path, output_format="source-only")
            client = FakeClient(['[{"end":4,"text":"版本就绪。"},{"end":6,"text":"继续。"}]',
                                 '[{"end":1,"text":"是的。"}]'])
            cfg = config(review_mode="tui")
            cfg.pipeline.threads = 1
            review = MergeReview()
            store = TranslationTaskStore(root / "tasks.db")
            service = TranslationService(cfg, translation_client=client,
                summary_client=FakeClient(['{"summary":"A tutorial.","terms":[],"source_corrections":[]}']),
                review_port=review, task_store=store)
            result = service.translate(TranslationRequest(str(input_path), str(root / "out.srt"), "Chinese"))
            assert result.report.task_id is not None
            self.assertEqual(review.calls, 1)
            self.assertEqual([e.index for e in result.subtitle.entries], [1, 2])
            self.assertEqual(result.report.removed_entry_indices, [5])
            before = (root / "out.srt").read_text()
            resumed = TranslationService(cfg, translation_client=FakeClient([]), summary_client=FakeClient([]),
                                         review_port=review, task_store=store).translate(
                TranslationRequest(None, None, "Chinese", resume=True, task_id=result.report.task_id))
            self.assertEqual(review.calls, 1)
            self.assertEqual(resumed.report.removed_entry_indices, [5])
            self.assertEqual((root / "out.srt").read_text(), before)
            saved_cues = store.load_resume_state(result.report.task_id)["entries"]
            self.assertEqual(set(saved_cues), {"1", "7"})

    def test_single_pass_asr_pipeline_and_task_id_resume(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            input_path = root / "input.srt"
            SubtitleIO.write_srt(Subtitle([
                entry(1, "We load the model into GPU memory.", 0, 3500),
                entry(2, "Then we send a prompt.", 3600, 7000),
                entry(3, "Yes.", 7100, 7500),
            ]), input_path, output_format="source-only")
            cfg = config()
            cfg.pipeline.model_segmentation = "auto"
            summary = FakeClient(['{"summary":"A tutorial.","terms":[],"source_corrections":[]}'])

            def response(prompt):
                match = re.search(r"Source positions (\d+)-(\d+):", prompt)
                assert match is not None
                first, last = map(int, match.groups())
                if last > 1:
                    return '[{"end":7,"text":"我们把模型加载到GPU显存中。"},{"end":12,"text":"然后发送提示词。"}]'
                return json.dumps([{"end":last,"text":"是的。"}], ensure_ascii=False)

            client = FakeClient([response, response])
            store = TranslationTaskStore(root / "tasks.db")
            service = TranslationService(cfg, translation_client=client, summary_client=summary, task_store=store)
            with patch.object(service, "_resolve_input", return_value=ResolvedInput(str(input_path), from_asr=True)):
                result = service.translate(TranslationRequest(str(input_path), str(root / "output.srt"), "Chinese"))
            self.assertEqual(len(client.prompts), 2)
            self.assertEqual(len(summary.prompts), 1)
            self.assertTrue(result.report.model_segmentation_applied)
            self.assertEqual([e.index for e in result.subtitle.entries], [1, 2, 3])
            self.assertEqual([e.original_text for e in result.subtitle.entries],
                             ["We load the model into GPU memory.", "Then we send a prompt.", "Yes."])
            self.assertEqual(result.subtitle.entries[-1].end_time, "00:00:07,500")
            self.assertEqual(result.report.token_usage.total_tokens, 360)
            assert result.report.normalization_map_file is not None
            saved_map = json.loads(Path(result.report.normalization_map_file).read_text())
            self.assertEqual([c["original_indices"] for c in saved_map["cues"]], [[1], [2], [3]])
            original_output = (root / "output.srt").read_text()
            resumed_service = TranslationService(config(), translation_client=FakeClient([]),
                                                  summary_client=FakeClient([]), task_store=store)
            resumed = resumed_service.translate(TranslationRequest(None, None, "Chinese", resume=True,
                                                                   task_id=result.report.task_id))
            self.assertEqual((root / "output.srt").read_text(), original_output)
            self.assertEqual(resumed.report.resumed_entries, 3)
            self.assertEqual(resumed.report.token_usage.total_tokens, 0)
            self.assertFalse(resumed.report.failed_chunks)

    def test_v2_database_upgrade_preserves_existing_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.db"
            store = TranslationTaskStore(path)
            record = store.create_task(input_display="input", working_directory=tmp, source_subtitle_path="input",
                                       normalized_input_fingerprint="abc", target_language="Chinese", source_language="en",
                                       output_format="source-first", output_file="output", config=config())
            with contextlib.closing(sqlite3.connect(path)) as conn:
                conn.execute("DROP TABLE translation_model_results")
                conn.execute("PRAGMA user_version = 2")
                conn.commit()
            upgraded = TranslationTaskStore(path)
            self.assertEqual(upgraded.get_task(record.task_id).normalized_input_fingerprint, "abc")
            upgraded.save_model_result(record.task_id, "key", {"pieces": []})
            self.assertEqual(upgraded.load_model_result(record.task_id, "key"), {"pieces": []})


if __name__ == "__main__":
    unittest.main()
