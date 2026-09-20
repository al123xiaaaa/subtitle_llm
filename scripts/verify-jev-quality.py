"""显式运行的联网验收：小型标注样例与真实 Python → Node → Gateway 质检链路。"""

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

CASES = [
    ("否定反转", "I don't think this will work.", "我认为这会奏效。", "meaning"),
    ("正确否定", "I don't think this will work.", "我觉得这行不通。", None),
    ("重要遗漏", "Press Save before closing, or all your changes will be lost.", "关闭窗口就行。", "omission"),
    ("无据增译", "The package arrived yesterday.", "包裹昨天到了，里面的电脑已经损坏。", "addition"),
    ("术语误译", "The CPU executes instructions.", "这块电池负责执行指令。", "terminology"),
    ("正确意译", "It's a piece of cake.", "这事很容易。", None),
]


def main():
    from subtitle_llm.domain import SubtitleEntry
    from subtitle_llm.pipeline.report import TranslationReport
    from subtitle_llm.pipeline.semantic_quality import JevSemanticReviewer, gateway_evaluate
    from tests.test_semantic_quality import run_fixture

    rows = []
    for label, source, translation, expected in CASES:
        report = TranslationReport(input_file="synthetic", output_file="synthetic", context_file="")
        reviewer = JevSemanticReviewer(context="", report=report)
        reviewer.check([SubtitleEntry(1, "00:00:00,000", "00:00:05,000", source, translation)], "Chinese")
        record = report.semantic_quality_checks[0]
        if record["status"] != "checked":
            raise RuntimeError("真实语义检查未完成")
        probabilities = {name: judgment["exists_probability"] for name, judgment in record["judgments"].items()}
        passed = (probabilities[expected] >= 0.9 if expected
                  else max(probabilities.values()) < 0.4)
        rows.append({"case": label, "passed": passed, "probabilities": probabilities, "usage": record["usage"]})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)

    with tempfile.TemporaryDirectory() as temporary:
        # 翻译输出固定，只有语义质检联网；验证判断实际进入字幕标记和任务状态。
        result, store, client = run_fixture(Path(temporary), gateway_evaluate)
        assert result.report.task_id is not None
        status = store.get_task(result.report.task_id).status
        integrated = status == "completed_with_warnings" and result.subtitle.entries[0].needs_retranslation and client.calls == 1
        print(json.dumps({"pipeline": status, "review_flag": result.subtitle.entries[0].needs_retranslation,
                          "translation_calls": client.calls, "passed": integrated}, ensure_ascii=False))
    if not integrated or not all(row["passed"] for row in rows):
        raise RuntimeError("样例验收未全部通过，请检查判断记录")
    print("6 个联网语义样例及真实质检流水线验收通过。")


if __name__ == "__main__":
    main()
