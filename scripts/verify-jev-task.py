"""重放任务的既有质检证据；默认离线规划，--live 才请求 Gateway，不修改原任务。"""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from subtitle_llm.domain import SubtitleEntry  # noqa: E402
from subtitle_llm.pipeline.chunks import PlannedChunk  # noqa: E402
from subtitle_llm.pipeline.report import TranslationReport  # noqa: E402
from subtitle_llm.pipeline.semantic_quality import (  # noqa: E402
    CONTEXT_BYTES, MAX_CHOICE_OPTIONS, JevSemanticReviewer, build_request, gateway_evaluate,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--database", type=Path, default=Path.home() / "Library/Application Support/subtitle-llm-desktop/translation-tasks.sqlite3")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chunks", type=int, nargs="+")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    groups = defaultdict(list)
    # 只读取必要的证据字段，不读取配置快照或认证信息。
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        for raw, in connection.execute(
            "SELECT payload_json FROM translation_model_results WHERE task_id=? AND request_key LIKE 'jev-quality:%'",
            (args.task_id,),
        ):
            record = json.loads(raw)
            state = record["request"]["state"]
            number = state.get("translation_context", {}).get("chunk_number")
            if number is not None and (not args.chunks or number in args.chunks):
                groups[number].append(record)
        cue_rows = connection.execute(
            "SELECT cue_index,start_time,end_time,original_text,translated_text FROM translation_cues WHERE task_id=? ORDER BY cue_index",
            (args.task_id,),
        ).fetchall()
    if not groups:
        raise ValueError("找不到包含完整 chunk 上下文的历史质检请求")
    cues = {row[0]: SubtitleEntry(*row) for row in cue_rows}
    output = {"task_id": args.task_id, "live": args.live, "chunks": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for number, records in sorted(groups.items()):
        state = records[0]["request"]["state"]
        if any(record["request"]["state"] != state for record in records):
            raise ValueError("同一片段存在多个证据版本，请先明确重放版本")
        entries = [cues[cue["cue_id"]] for cue in state["cues"].values()]
        if any(entry.original_text != cue["source"] or entry.translated_text != cue["translation"]
               for entry, cue in zip(entries, state["cues"].values())):
            raise ValueError("历史请求与当前持久化字幕不同，不能混合比较")
        scope = state["translation_context"]
        payload = build_request(entries, state["target_language"], state["generated_context"], translation_context=scope)
        request_bytes = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
        if request_bytes > CONTEXT_BYTES or len(payload["questions"]["meaning_where"]["criteria"]) > MAX_CHOICE_OPTIONS:
            raise ValueError("完整片段超出当前请求预算，不裁剪后继续验收")
        row = {
            "chunk": number, "cues": len(entries), "question_count": len(payload["questions"]),
            "request_bytes": request_bytes, "old_requests": len(records),
            "old_tokens": sum(record.get("usage", {}).get("totalTokens", 0) for record in records),
        }
        if args.live:
            calls = []
            def evaluate(request):
                calls.append(request)
                return gateway_evaluate(request)
            report = TranslationReport(input_file="replay", output_file="", context_file="", total_chunks=scope["total_chunks"])
            reviewer = JevSemanticReviewer(
                context=state["generated_context"], report=report, evaluate=evaluate,
                source_kind=scope["source_kind"], source_language=scope.get("source_language"),
                segmentation_context={number - 1: (scope["readonly_before"], scope["readonly_after"])}
                if "readonly_before" in scope else None,
            )
            planned = PlannedChunk(number - 1, entries, scope["readonly_boundary_context"], [])
            issues = reviewer.check(entries, state["target_language"], planned=planned)
            record = report.semantic_quality_checks[0]
            assert len(calls) == 1 and calls[0] == payload
            reviewer.check(entries, state["target_language"], planned=planned)
            assert len(calls) == 1 and len(report.semantic_quality_checks) == 1
            row.update(record)
            row["candidate_cue_ids"] = sorted({entries[issue.index - 1].index for issue in issues})
            row["request"] = payload
        output["chunks"].append(row)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({key: row[key] for key in ("chunk", "cues", "question_count", "request_bytes", "old_requests", "old_tokens", "status", "usage") if key in row}, ensure_ascii=False), flush=True)
        if args.live and row["status"] != "checked":
            raise RuntimeError("真实检查未完成，已保存安全的状态记录")
    print(f"验收记录已保存：{args.output}")


if __name__ == "__main__":
    main()
