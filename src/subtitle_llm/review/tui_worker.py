from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    package_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(package_root))

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.review.custom_handling import CustomHandlingApp
from subtitle_llm.runtime_logging import RUN_LOG_ENV, configure_logging_from_env

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INPUT_FILE = "input.json"
OUTPUT_FILE = "output.json"
INPUT_READY = "input.ready"
OUTPUT_READY = "output.ready"
DONE_FILE = "done"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python tui_worker.py <comm_dir> [run_log_file]")
        sys.exit(1)
    if len(sys.argv) >= 3:
        os.environ[RUN_LOG_ENV] = sys.argv[2]
    configure_logging_from_env()

    comm_dir = Path(sys.argv[1])
    input_path = comm_dir / INPUT_FILE
    output_path = comm_dir / OUTPUT_FILE
    input_ready = comm_dir / INPUT_READY
    output_ready = comm_dir / OUTPUT_READY
    done_path = comm_dir / DONE_FILE

    print("TUI Worker 已启动，等待审核数据...")
    logger.info("TUI Worker 已启动: comm_dir=%s", comm_dir)
    while True:
        if done_path.exists():
            print("收到结束信号，Worker 退出。")
            logger.info("TUI Worker 收到结束信号")
            break
        if not input_ready.exists():
            time.sleep(0.5)
            continue

        try:
            data = json.loads(input_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError) as exc:
            logger.error("读取输入数据失败: %s", exc)
            time.sleep(0.5)
            continue

        subtitle_entries = [
            SubtitleEntry.from_dict(entry) for entry in data["subtitle_entries"]
        ]
        chunk_index = data.get("chunk_index", 0)
        total_chunks = data.get("total_chunks", 1)
        print(f"\n=== Chunk {chunk_index + 1}/{total_chunks} ===")
        logger.info("TUI Worker 接收chunk: chunk=%s/%s entries=%s", chunk_index + 1, total_chunks, len(subtitle_entries))

        app = CustomHandlingApp(
            subtitle_entries,
            temp_file_path=str(output_path),
            chunk_index=chunk_index,
            total_chunks=total_chunks,
        )
        app.run()

        if output_path.exists():
            try:
                result = json.loads(output_path.read_text(encoding="utf-8"))
                result["tui_completed"] = True
                output_path.write_text(json.dumps(result, ensure_ascii=False, indent=4), encoding="utf-8")
            except Exception:
                pass

        output_ready.write_text("ready", encoding="utf-8")
        input_ready.unlink(missing_ok=True)
        print(f"Chunk {chunk_index + 1} 审核完成，等待下一个...")
        logger.info("TUI Worker 完成chunk: chunk=%s/%s", chunk_index + 1, total_chunks)

    for path in [input_path, output_path, input_ready, output_ready, done_path]:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
