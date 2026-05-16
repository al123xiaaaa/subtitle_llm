"""
TUI Worker - 在独立 Terminal 中运行，通过文件通信排队处理多个 chunk。

用法: python tui_worker.py <comm_dir>

协议:
  主进程写入 comm_dir/input.json + 创建 input.ready 标记
  Worker 检测后读取、运行 TUI、写入 output.json + 创建 output.ready
  主进程读取结果后删除标记
  所有完成后主进程创建 done 标记，Worker 退出
"""
import sys
import os
import json
import time
import logging

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.models.subtitle_entry import SubtitleEntry
from src.services.tui.custom_handling import CustomHandlingApp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INPUT_FILE = "input.json"
OUTPUT_FILE = "output.json"
INPUT_READY = "input.ready"
OUTPUT_READY = "output.ready"
DONE_FILE = "done"


def main():
    if len(sys.argv) < 2:
        print("Usage: python tui_worker.py <comm_dir>")
        sys.exit(1)

    comm_dir = sys.argv[1]
    input_path = os.path.join(comm_dir, INPUT_FILE)
    output_path = os.path.join(comm_dir, OUTPUT_FILE)
    input_ready = os.path.join(comm_dir, INPUT_READY)
    output_ready = os.path.join(comm_dir, OUTPUT_READY)
    done_path = os.path.join(comm_dir, DONE_FILE)

    print("TUI Worker 已启动，等待审核数据...")

    while True:
        # 检查是否结束
        if os.path.exists(done_path):
            print("收到结束信号，Worker 退出。")
            break

        # 等待输入
        if not os.path.exists(input_ready):
            time.sleep(0.5)
            continue

        # 读取数据
        try:
            with open(input_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.error(f"读取输入数据失败: {e}")
            time.sleep(0.5)
            continue

        subtitle_entries = [
            SubtitleEntry.from_dict(entry) for entry in data["subtitle_entries"]
        ]
        chunk_index = data.get("chunk_index", 0)
        total_chunks = data.get("total_chunks", 1)
        print(f"\n=== Chunk {chunk_index + 1}/{total_chunks} ===")

        # 运行 TUI
        app = CustomHandlingApp(
            subtitle_entries,
            temp_file_path=output_path,
        )
        app.run()

        # TUI 完成后，读取结果（on_quit 已经写入了 output_path）
        # 如果文件存在且有 tui_completed，直接用
        if os.path.exists(output_path):
            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    result = json.load(f)
                # 确保 tui_completed 标记存在
                result["tui_completed"] = True
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=4)
            except Exception:
                pass

        # 创建 output ready 标记
        with open(output_ready, "w") as f:
            f.write("ready")

        # 删除 input ready 标记
        if os.path.exists(input_ready):
            os.remove(input_ready)

        print(f"Chunk {chunk_index + 1} 审核完成，等待下一个...")

    # 清理
    for f in [input_path, output_path, input_ready, output_ready, done_path]:
        p = os.path.join(comm_dir, f)
        if os.path.exists(p):
            os.remove(p)


if __name__ == "__main__":
    main()
