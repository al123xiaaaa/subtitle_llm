"""prompt 缓存字段采集测试。

验证 DeepSeek 等 API 返回的 prompt_cache_hit_tokens / prompt_cache_miss_tokens
在 CompletionUsage、TokenUsage 各层被正确采集与汇总，不被丢弃。
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.llm.types import CompletionUsage
from subtitle_llm.pipeline.report import TokenUsage


class TestCompletionUsageCacheFields(unittest.TestCase):
    """CompletionUsage 采集 prompt 缓存字段。"""

    def test_from_any_dict_captures_cache_fields(self):
        """DeepSeek 响应 usage（dict 形式）含缓存字段时被采集。"""
        usage = CompletionUsage.from_any({
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
            "prompt_cache_hit_tokens": 800,
            "prompt_cache_miss_tokens": 200,
        })
        self.assertEqual(usage.prompt_cache_hit_tokens, 800)
        self.assertEqual(usage.prompt_cache_miss_tokens, 200)

    def test_from_any_object_captures_cache_fields(self):
        """OpenAI SDK 对象（带属性）含缓存字段时被采集。"""

        class FakeUsage:
            prompt_tokens = 1000
            completion_tokens = 200
            total_tokens = 1200
            prompt_cache_hit_tokens = 950
            prompt_cache_miss_tokens = 50

        usage = CompletionUsage.from_any(FakeUsage())
        self.assertEqual(usage.prompt_cache_hit_tokens, 950)
        self.assertEqual(usage.prompt_cache_miss_tokens, 50)

    def test_from_any_missing_cache_fields_defaults_zero(self):
        """非 DeepSeek 厂商（如 Gemini）不返回缓存字段时，默认为 0。"""
        usage = CompletionUsage.from_any({
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
        })
        self.assertEqual(usage.prompt_cache_hit_tokens, 0)
        self.assertEqual(usage.prompt_cache_miss_tokens, 0)

    def test_from_any_none_returns_empty(self):
        usage = CompletionUsage.from_any(None)
        self.assertEqual(usage.prompt_cache_hit_tokens, 0)
        self.assertEqual(usage.prompt_cache_miss_tokens, 0)

    def test_to_dict_includes_cache_fields(self):
        """to_dict（供 trace JSON 用）输出缓存字段。"""
        usage = CompletionUsage(
            prompt_tokens=1000,
            completion_tokens=200,
            total_tokens=1200,
            prompt_cache_hit_tokens=800,
            prompt_cache_miss_tokens=200,
        )
        data = usage.to_dict()
        self.assertEqual(data["prompt_cache_hit_tokens"], 800)
        self.assertEqual(data["prompt_cache_miss_tokens"], 200)

    def test_add_accumulates_cache_fields(self):
        """add（多次调用累加）正确累加缓存字段。"""
        a = CompletionUsage(prompt_cache_hit_tokens=100, prompt_cache_miss_tokens=50)
        b = CompletionUsage(prompt_cache_hit_tokens=200, prompt_cache_miss_tokens=30)
        a.add(b)
        self.assertEqual(a.prompt_cache_hit_tokens, 300)
        self.assertEqual(a.prompt_cache_miss_tokens, 80)


class TestTokenUsageCacheFields(unittest.TestCase):
    """report.TokenUsage 汇总缓存字段并计算命中率。"""

    def test_add_usage_aggregates_cache_fields(self):
        """add_usage 从 CompletionUsage 累加缓存字段。"""
        report = TokenUsage()
        report.add_usage(CompletionUsage(
            prompt_tokens=1000, completion_tokens=200, total_tokens=1200,
            prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=200,
        ))
        report.add_usage(CompletionUsage(
            prompt_tokens=500, completion_tokens=100, total_tokens=600,
            prompt_cache_hit_tokens=400, prompt_cache_miss_tokens=100,
        ))
        self.assertEqual(report.prompt_cache_hit_tokens, 1200)
        self.assertEqual(report.prompt_cache_miss_tokens, 300)

    def test_cache_hit_rate_with_hits(self):
        report = TokenUsage(prompt_cache_hit_tokens=900, prompt_cache_miss_tokens=100)
        self.assertEqual(report.cache_hit_rate(), 90)

    def test_cache_hit_rate_zero_when_no_cache_data(self):
        """无缓存数据（非 DeepSeek）时命中率为 0，不除零。"""
        report = TokenUsage()
        self.assertEqual(report.cache_hit_rate(), 0)


if __name__ == "__main__":
    unittest.main()
