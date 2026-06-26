"""翻译响应的 JSON 兜底解析与解析失败重试测试。

验证两件事：
1. ``parse_translation_json`` 在标准 ``json.loads`` 失败时，用 ``json-repair`` 挽救
   残缺/语法错的 JSON（模型提前停止、把思考混进字段值等场景）。
2. ``translate_semantic_timed_cues`` 在响应解析失败时重试，重试成功则整片不回退原文。
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.llm.types import CompletionResult, CompletionUsage
from subtitle_llm.pipeline.chunk_translator import ChunkTranslator
from subtitle_llm.pipeline.semantic_units import SemanticUnit, make_unit
from subtitle_llm.pipeline.text import parse_translation_json
from subtitle_llm.settings import ModelConfig, ModelProvider


def make_entry(index: int, text: str) -> SubtitleEntry:
    return SubtitleEntry(
        index=index,
        start_time="00:00:00,000",
        end_time="00:00:01,000",
        original_text=text,
    )


class TestParseTranslationJsonRepair(unittest.TestCase):
    """标准解析失败时 json-repair 兜底。"""

    def test_parses_valid_json_directly(self):
        content = '{"translations": [{"cue_id": 1, "translation": "你好"}]}'
        data = parse_translation_json(content)
        self.assertEqual(data["translations"][0]["translation"], "你好")

    def test_repairs_truncated_unclosed_json(self):
        """模型提前停止、JSON 未闭合（缺 ]}）—— json-repair 补全。"""
        # 缺少数组闭合 ] 和对象闭合 }
        truncated = (
            '{"translations": [\n'
            '    {"cue_id": 1, "translation": "你好"},\n'
            '    {"cue_id": 2, "translation": "世界"}}'  # 末尾 }} 缺 ]
        )
        data = parse_translation_json(truncated)
        self.assertEqual(len(data["translations"]), 2)
        self.assertEqual(data["translations"][1]["translation"], "世界")

    def test_repairs_thinking_leaked_into_field(self):
        """模型把思考过程写进 translation 字段 —— json-repair 收敛。"""
        leaked = (
            '{"translations": [\n'
            '    {"cue_id": 1, "translation": "你好"},\n'
            '    {"cue_id": 2, "translation": "，"},  // 这只是逗号，需要翻译吗？可能保留\n'
            '    {"cue_id": 3, "translation": "再见"}\n'
            "]}"
        )
        data = parse_translation_json(leaked)
        self.assertEqual(len(data["translations"]), 3)

    def test_unrepairable_json_reraises(self):
        """完全无法解析的内容仍抛错，交由上层重试或回退。"""
        with self.assertRaises(Exception):
            parse_translation_json("this is not json at all")


class _ScriptedClient:
    """按脚本依次返回预设响应的假 client，用于重试测试。"""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.call_count = 0

    def create_completion(self, config, messages):
        idx = min(self.call_count, len(self.responses) - 1)
        self.call_count += 1
        return CompletionResult(
            content=self.responses[idx],
            usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


class TestSemanticTimedCuesRetry(unittest.TestCase):
    """translate_semantic_timed_cues 解析失败时重试。"""

    def _make_translator(self, client: _ScriptedClient) -> ChunkTranslator:
        config = ModelConfig(
            type=ModelProvider.CUSTOM,
            api_key_env="FAKE_KEY",
            model="fake",
            endpoint="https://fake.test",
        )
        return ChunkTranslator(client, config)

    def _make_units(self) -> list[SemanticUnit]:
        entries = [
            make_entry(1, "hello"),
            make_entry(2, "world"),
        ]
        return [make_unit(1, entries)]

    def test_retries_then_succeeds_on_bad_then_good_response(self):
        """第一次返回残缺 JSON，重试后返回合法 JSON —— 整体成功。"""
        bad = '{"translations": [{"cue_id": 1, "translation": "你好"}'  # 截断，cue 2 缺失
        good = (
            '{"translations": ['
            '{"cue_id": 1, "translation": "你好"}, '
            '{"cue_id": 2, "translation": "世界"}'
            "]}"
        )
        client = _ScriptedClient([bad, good])
        translator = self._make_translator(client)

        result = translator.translate_semantic_timed_cues(
            self._make_units(),
            context="",
            target_language="Chinese",
            boundary_context="",
        )

        self.assertEqual(client.call_count, 2)  # 第一次失败，重试一次成功
        self.assertIn("你好", result.translation)
        self.assertIn("世界", result.translation)

    def test_exhausts_retries_then_raises(self):
        """连续返回坏响应，用尽重试后抛出最后一个错误。"""
        bad = '{"translations": [{"cue_id": 1, "translation": "你好"}'  # 始终截断
        client = _ScriptedClient([bad, bad, bad])
        translator = self._make_translator(client)

        with self.assertRaises(Exception):
            translator.translate_semantic_timed_cues(
                self._make_units(),
                context="",
                target_language="Chinese",
                boundary_context="",
            )
        # 1 次初试 + 2 次重试 = 3 次
        self.assertEqual(client.call_count, 3)

    def test_succeeds_first_try_no_retry(self):
        """合法响应不触发重试。"""
        good = (
            '{"translations": ['
            '{"cue_id": 1, "translation": "你好"}, '
            '{"cue_id": 2, "translation": "世界"}'
            "]}"
        )
        client = _ScriptedClient([good])
        translator = self._make_translator(client)

        result = translator.translate_semantic_timed_cues(
            self._make_units(),
            context="",
            target_language="Chinese",
            boundary_context="",
        )

        self.assertEqual(client.call_count, 1)
        self.assertIn("你好", result.translation)


if __name__ == "__main__":
    unittest.main()
