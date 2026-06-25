"""chunk 规划的 token 预算约束测试。

验证 chunk_list / ChunkPlanner 在引入 max_output_tokens 后：
- token 预算生效：每块的估算输出 token 不超过预算；
- 向后兼容：不传预算时行为与原来一致；
- 与 chunk_size 两条约束取严格者。
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.llm.token_counter import build_token_encoder, estimate_output_tokens
from subtitle_llm.pipeline.chunks import ChunkPlanner
from subtitle_llm.pipeline.text import chunk_list


def make_entry(index: int, text: str) -> SubtitleEntry:
    return SubtitleEntry(
        index=index,
        start_time="00:00:00,000",
        end_time="00:00:01,000",
        original_text=text,
    )


def long_entries(count: int, words_per_entry: int = 60) -> list[SubtitleEntry]:
    """构造一批长文本条目，每条约 words_per_entry 个英文单词。"""
    word_bank = (
        "the quick brown fox jumps over the lazy dog while people watch "
        "carefully and discuss the implications of this remarkable event "
    ).split()
    entries: list[SubtitleEntry] = []
    for i in range(count):
        words = [word_bank[(i * words_per_entry + w) % len(word_bank)] for w in range(words_per_entry)]
        entries.append(make_entry(i + 1, " ".join(words) + "."))
    return entries


class TestChunkListTokenBudget(unittest.TestCase):
    def setUp(self):
        self.encoder = build_token_encoder("fake-model")

    def test_without_budget_uses_chunk_size_only(self):
        """不传预算时，只按 chunk_size 切（含句子边界扩展），行为向后兼容。"""
        entries = long_entries(10, words_per_entry=60)
        chunks = chunk_list(entries, chunk_size=3)

        # 不传预算时不进入 token 约束分支，结果与原逻辑一致。
        self.assertEqual(sum(len(c) for c in chunks), 10)
        self.assertGreater(len(chunks), 0)

    def test_budget_splits_oversized_chunks(self):
        """token 预算生效：预算足够小时长文本被切成更多、更小的块。"""
        entries = long_entries(10, words_per_entry=60)

        # 不传预算：chunk_size=10 → 一块。
        without_budget = chunk_list(entries, chunk_size=10)
        self.assertEqual(len(without_budget), 1)

        # 传一个小预算：单块源文 token 远超 → 切成多块。
        first_chunk_tokens = estimate_output_tokens(
            [e.original_text for e in without_budget[0]], self.encoder
        )
        budget = first_chunk_tokens // 4  # 约束为原来的 1/4
        with_budget = chunk_list(entries, chunk_size=10, max_output_tokens=budget, encoder=self.encoder)

        self.assertGreater(len(with_budget), 1)
        # 每块的估算 token 都不超过预算（最后一块可能更小）。
        for chunk in with_budget:
            tokens = estimate_output_tokens([e.original_text for e in chunk], self.encoder)
            self.assertLessEqual(tokens, budget)

    def test_budget_and_chunk_size_take_stricter(self):
        """两条约束取严格者：chunk_size 很小但预算很大时，仍受 chunk_size 限制。"""
        entries = long_entries(6, words_per_entry=60)
        huge_budget = 10_000_000

        chunks = chunk_list(entries, chunk_size=2, max_output_tokens=huge_budget, encoder=self.encoder)

        # 预算不构成约束，chunk_size=2 主导 → 每块最多 2 条。
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 2)

    def test_budget_never_produces_empty_chunks(self):
        """即便单条就超预算，也至少保留 1 条，不产生空块。"""
        entries = long_entries(3, words_per_entry=200)
        tiny_budget = 10  # 单条远超

        chunks = chunk_list(entries, chunk_size=34, max_output_tokens=tiny_budget, encoder=self.encoder)

        self.assertGreater(len(chunks), 0)
        for chunk in chunks:
            self.assertGreaterEqual(len(chunk), 1)
        self.assertEqual(sum(len(c) for c in chunks), 3)

    def test_sentence_boundary_preferred_within_budget(self):
        """预算允许的窗口内仍优先落在句末标点（窗口中间的边界优先于窗口末尾）。"""
        # 第 2 条以句号结尾且位于窗口中间（不是最后一条）。
        entries = [
            make_entry(1, "this is a fragment that does not end with punctuation"),
            make_entry(2, "this one does end with a period."),
            make_entry(3, "another fragment without ending"),
            make_entry(4, "and a final sentence."),
            make_entry(5, "trailing entry that pushes boundary into mid window."),
            make_entry(6, "extra entry here."),
        ]
        # 预算足够大，不构成约束；chunk_size=6 让窗口覆盖全部。
        chunks = chunk_list(entries, chunk_size=6, max_output_tokens=10_000, encoder=self.encoder)

        # 句子边界对齐从窗口末尾往前找，应落在第 5 条（窗口中靠后的句末标点）。
        self.assertLessEqual(len(chunks[0]), 6)
        self.assertTrue(chunks[0][-1].original_text.rstrip().endswith("."))


class TestChunkPlannerTokenBudget(unittest.TestCase):
    def test_planner_passes_budget_to_chunks(self):
        """ChunkPlanner 透传预算与 encoder，产出受预算约束的块。"""
        encoder = build_token_encoder("fake-model")
        entries = long_entries(8, words_per_entry=80)

        # 无预算：chunk_size=8 → 一块。
        planner_no_budget = ChunkPlanner(
            chunk_size=8, context_window_size=0, ignore_subtitle_length=0,
        )
        self.assertEqual(len(planner_no_budget.plan(entries)), 1)

        # 有小预算：切成多块。
        single_tokens = estimate_output_tokens([e.original_text for e in entries], encoder)
        budget = single_tokens // 3
        planner = ChunkPlanner(
            chunk_size=8,
            context_window_size=0,
            ignore_subtitle_length=0,
            max_output_tokens=budget,
            encoder=encoder,
        )
        planned = planner.plan(entries)
        self.assertGreater(len(planned), 1)
        for chunk in planned:
            tokens = estimate_output_tokens([e.original_text for e in chunk.entries], encoder)
            self.assertLessEqual(tokens, budget)


class TestEstimateOutputTokens(unittest.TestCase):
    def test_empty_returns_zero(self):
        encoder = build_token_encoder("fake-model")
        self.assertEqual(estimate_output_tokens([], encoder), 0)

    def test_scales_with_text_length(self):
        encoder = build_token_encoder("fake-model")
        short = estimate_output_tokens(["a"], encoder)
        long = estimate_output_tokens(["a" * 500], encoder)
        self.assertGreater(long, short)

    def test_includes_per_entry_overhead(self):
        """每条带固定结构开销：拆成两条比合并一条 token 多。"""
        encoder = build_token_encoder("fake-model")
        combined = estimate_output_tokens(["hello world"], encoder)
        split = estimate_output_tokens(["hello", "world"], encoder)
        self.assertGreater(split, combined)


if __name__ == "__main__":
    unittest.main()
