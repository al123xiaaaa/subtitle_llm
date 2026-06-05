import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.progress_events import PROGRESS_EVENT_PREFIX, ProgressEmitter, chunk_payload
from subtitle_llm.runtime_logging import configure_run_logging


class TestProgressEvents(unittest.TestCase):
    def test_progress_event_is_json_line_and_written_to_run_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = configure_run_logging("progress", Path(tmp) / "logs")
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                payload = ProgressEmitter("translate").emit(
                    stage="processing_chunks",
                    detail="refine",
                    label="润色",
                    message="正在润色第 1/2 个片段",
                    chunk={"index": 1, "total": 2, "status": "running"},
                    model={"provider": "deepseek", "name": "deepseek-v4-flash"},
                    usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
                    trace_id="000001",
                )

            line = output.getvalue().strip()
            self.assertTrue(line.startswith(PROGRESS_EVENT_PREFIX))
            parsed = json.loads(line.removeprefix(PROGRESS_EVENT_PREFIX))
            self.assertEqual(parsed["command"], "translate")
            self.assertEqual(parsed["detail"], "refine")
            self.assertEqual(parsed["chunk"]["status"], "running")
            self.assertEqual(parsed["model"]["name"], "deepseek-v4-flash")
            self.assertEqual(payload["trace_id"], "000001")
            self.assertIn("进度事件", log_path.read_text(encoding="utf-8"))

    def test_chunk_payload_uses_user_visible_one_based_index(self):
        from subtitle_llm.domain import SubtitleEntry

        entries = [
            SubtitleEntry(154, "00:00:00,000", "00:00:01,000", "A"),
            SubtitleEntry(182, "00:00:01,000", "00:00:02,000", "B"),
        ]

        payload = chunk_payload(
            entries,
            chunk_index=5,
            total_chunks=16,
            status="review",
            detail="tui_wait",
            issue_summary="29 行疑似缺失",
        )

        self.assertEqual(payload["index"], 6)
        self.assertEqual(payload["total"], 16)
        self.assertEqual(payload["entry_start"], 154)
        self.assertEqual(payload["entry_end"], 182)
        self.assertEqual(payload["status"], "review")


if __name__ == "__main__":
    unittest.main()
