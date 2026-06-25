import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.pipeline.context import parse_context_components, parse_context_response


class TestContextParsing(unittest.TestCase):
    def test_parses_terms_and_asr_corrections(self):
        summary, terms, corrections = parse_context_response(
            "总结: 讲述一段产品发布演示。\n\n"
            "短语术语:\n"
            "- LaunchKit(LaunchKit)\n"
            "- Model X2(Model X2)\n\n"
            "疑似ASR修正:\n"
            "- lunch kit -> LaunchKit(LaunchKit) [confidence: high]\n"
            "- model ex two -> Model X2(Model X2) [confidence: high]\n"
        )

        self.assertEqual(summary, "讲述一段产品发布演示。")
        self.assertEqual(terms, ["LaunchKit(LaunchKit)", "Model X2(Model X2)"])
        self.assertEqual(corrections, [
            "lunch kit -> LaunchKit(LaunchKit) [confidence: high]",
            "model ex two -> Model X2(Model X2) [confidence: high]",
        ])

    def test_omits_none_asr_corrections(self):
        _summary, _terms, corrections = parse_context_response(
            "总结: demo\n\n"
            "短语术语:\n"
            "- API(接口)\n\n"
            "疑似ASR修正:\n"
            "- (none)\n"
        )

        self.assertEqual(corrections, [])

    def test_parses_structured_source_corrections_from_json(self):
        summary, terms, corrections, source_corrections = parse_context_components(
            """```json
{
  "summary": "讲述都铎伦敦的街道与旅馆。",
  "terms": [{"source": "Cheapside", "target": "齐普赛街"}],
  "source_corrections": [
    {
      "cue_ids": [30],
      "observed": "cheap side",
      "corrected": "Cheapside",
      "type": "street",
      "enforcement": "hard",
      "target_aliases": ["齐普赛街", "Cheapside"],
      "confidence": "high",
      "evidence": "The next sentence calls it the main market street of Tudor London."
    }
  ]
}
```"""
        )

        self.assertEqual(summary, "讲述都铎伦敦的街道与旅馆。")
        self.assertEqual(terms, ["Cheapside(齐普赛街)"])
        self.assertEqual(corrections, ["cheap side -> Cheapside(齐普赛街) [confidence: high]"])
        self.assertEqual(len(source_corrections), 1)
        self.assertEqual(source_corrections[0].observed, "cheap side")
        self.assertEqual(source_corrections[0].corrected, "Cheapside")
        self.assertEqual(source_corrections[0].correction_type, "street")
        self.assertEqual(source_corrections[0].enforcement, "hard")


if __name__ == "__main__":
    unittest.main()
