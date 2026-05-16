import subprocess
import os
import platform
import tempfile
import json
import logging
import time
import shlex

logger = logging.getLogger(__name__)

INPUT_FILE = "input.json"
OUTPUT_FILE = "output.json"
INPUT_READY = "input.ready"
OUTPUT_READY = "output.ready"
DONE_FILE = "done"


class TUIManager:
    def __init__(self, run_script_path=None, width=300, height=52):
        self.width = width
        self.height = height
        self.worker_script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "tui_worker.py"
        )
        self.comm_dir = None
        self.worker_started = False

    def _ensure_worker(self):
        """确保 Worker Terminal 已启动。只启动一次。"""
        if self.worker_started:
            return

        self.comm_dir = tempfile.mkdtemp(prefix="tui_comm_")
        worker_script = self.worker_script
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(self.worker_script), "..", "..", "..")
        )
        venv_activate = self._find_virtualenv_activate(project_root)

        system = platform.system()
        command = f"python {worker_script} {self.comm_dir}"

        if system == "Darwin":
            if venv_activate:
                apple_script = f"""
                tell application "Terminal"
                    do script "printf '\\\\e[8;{self.height};{self.width}t' && source {shlex.quote(venv_activate)} && cd {shlex.quote(project_root)} && {command}"
                    activate
                end tell
                """
            else:
                apple_script = f"""
                tell application "Terminal"
                    do script "printf '\\\\e[8;{self.height};{self.width}t' && cd {shlex.quote(project_root)} && {command}"
                    activate
                end tell
                """
            subprocess.run(["osascript", "-e", apple_script], check=True)
        elif system == "Linux":
            cmd = f"cd {shlex.quote(project_root)} && {command}; exec bash"
            if venv_activate:
                cmd = f"source {shlex.quote(venv_activate)} && " + cmd
            subprocess.Popen(
                ["x-terminal-emulator", "-e", "bash", "-c", cmd],
                shell=False,
            )
        elif system == "Windows":
            cmd = f'cd /d "{project_root}" && {command}'
            if venv_activate:
                cmd = f'"{venv_activate}" && ' + cmd
            subprocess.run(f'start cmd /k "{cmd}"', shell=True, check=True)

        self.worker_started = True
        logger.info(f"TUI Worker 启动，通信目录: {self.comm_dir}")

    def submit_chunk(self, data, chunk_index=0, total_chunks=1):
        """
        提交一个 chunk 到 Worker 处理，等待结果返回。
        所有 chunk 在同一个 Terminal 中排队处理。
        """
        self._ensure_worker()

        input_path = os.path.join(self.comm_dir, INPUT_FILE)
        input_ready = os.path.join(self.comm_dir, INPUT_READY)
        output_path = os.path.join(self.comm_dir, OUTPUT_FILE)
        output_ready = os.path.join(self.comm_dir, OUTPUT_READY)

        # 清理上一次的输出标记
        if os.path.exists(output_ready):
            os.remove(output_ready)
        if os.path.exists(output_path):
            os.remove(output_path)

        # 写入输入数据
        payload = {
            "subtitle_entries": data,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
        }
        with open(input_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=4)

        # 通知 Worker 读取
        with open(input_ready, "w") as f:
            f.write("ready")

        logger.info(f"Chunk {chunk_index + 1}/{total_chunks} 已提交到 TUI Worker")

        # 等待 Worker 处理完成
        while not os.path.exists(output_ready):
            if not self.worker_started:
                return None
            time.sleep(0.5)

        # 读取结果
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                result = json.load(f)
            return result
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.error(f"读取 TUI 结果失败: {e}")
            return None

    def stop(self):
        """通知 Worker 退出。"""
        if not self.worker_started or not self.comm_dir:
            return

        done_path = os.path.join(self.comm_dir, DONE_FILE)
        with open(done_path, "w") as f:
            f.write("done")

        logger.info("已发送结束信号到 TUI Worker")
        self.worker_started = False

    # 保留旧接口兼容性
    def open_new_terminal(self, data):
        return self.submit_chunk(data)

    def _find_virtualenv_activate(self, project_root):
        possible = [
            os.path.join(project_root, "venv"),
            os.path.join(project_root, ".venv"),
            os.path.expanduser("~/.virtualenvs/subtitle_llm"),
        ]
        for loc in possible:
            if platform.system() == "Windows":
                for name in ("activate.bat", "Activate.ps1"):
                    p = os.path.join(loc, "Scripts", name)
                    if os.path.exists(p):
                        return p
            else:
                p = os.path.join(loc, "bin", "activate")
                if os.path.exists(p):
                    return p
        return None
