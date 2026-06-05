from __future__ import annotations

import json
import logging
import os
import platform
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from subtitle_llm.runtime_logging import RUN_LOG_ENV

logger = logging.getLogger(__name__)

INPUT_FILE = "input.json"
OUTPUT_FILE = "output.json"
INPUT_READY = "input.ready"
OUTPUT_READY = "output.ready"
DONE_FILE = "done"


class TUIManager:
    def __init__(self, width: int = 140, height: int = 42):
        self.width = width
        self.height = height
        self.worker_script = Path(__file__).with_name("tui_worker.py")
        self.comm_dir: str | None = None
        self.worker_started = False

    def submit_chunk(
        self,
        data,
        chunk_index: int = 0,
        total_chunks: int = 1,
        completed_chunks: int = 0,
    ):
        self._ensure_worker()
        assert self.comm_dir is not None
        logger.info(
            "提交TUI审核: chunk=%s/%s completed=%s entries=%s",
            chunk_index + 1,
            total_chunks,
            completed_chunks,
            len(data),
        )

        input_path = Path(self.comm_dir) / INPUT_FILE
        input_ready = Path(self.comm_dir) / INPUT_READY
        output_path = Path(self.comm_dir) / OUTPUT_FILE
        output_ready = Path(self.comm_dir) / OUTPUT_READY

        output_ready.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)

        payload = {
            "subtitle_entries": data,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "completed_chunks": completed_chunks,
        }
        input_path.write_text(json.dumps(payload, ensure_ascii=False, indent=4), encoding="utf-8")
        input_ready.write_text("ready", encoding="utf-8")

        while not output_ready.exists():
            if not self.worker_started:
                return None
            time.sleep(0.5)

        try:
            return json.loads(output_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError) as exc:
            logger.error("读取 TUI 结果失败: %s", exc)
            return None

    def stop(self) -> None:
        if not self.worker_started or not self.comm_dir:
            return
        (Path(self.comm_dir) / DONE_FILE).write_text("done", encoding="utf-8")
        self.worker_started = False
        logger.info("TUI Worker 停止: 通信目录=%s", self.comm_dir)

    def _ensure_worker(self) -> None:
        if self.worker_started:
            return

        self.comm_dir = tempfile.mkdtemp(prefix="subtitle_llm_tui_")
        cwd = Path.cwd()
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(self.worker_script))} {shlex.quote(self.comm_dir)}"
        run_log = os.getenv(RUN_LOG_ENV)
        if run_log:
            command = f"{command} {shlex.quote(run_log)}"
        activate = self._find_virtualenv_activate(cwd)
        if activate:
            command = f"source {shlex.quote(str(activate))} && {command}"

        system = platform.system()
        if system == "Darwin":
            apple_script = f"""
            tell application "Terminal"
                do script "printf '\\\\e[8;{self.height};{self.width}t' && cd {shlex.quote(str(cwd))} && {command}"
                activate
            end tell
            """
            subprocess.run(["osascript", "-e", apple_script], check=True)
        elif system == "Linux":
            subprocess.Popen(
                ["x-terminal-emulator", "-e", "bash", "-c", f"cd {shlex.quote(str(cwd))} && {command}; exec bash"],
                shell=False,
            )
        elif system == "Windows":
            subprocess.run(f'start cmd /k "cd /d {cwd} && {command}"', shell=True, check=True)
        else:
            raise RuntimeError(f"Unsupported platform for TUI review: {system}")

        self.worker_started = True
        logger.info("TUI Worker 启动，通信目录: %s", self.comm_dir)

    def _find_virtualenv_activate(self, project_root: Path) -> Path | None:
        candidates = [
            project_root / ".venv",
            project_root / "venv",
            Path.home() / ".virtualenvs" / "subtitle_llm",
        ]
        for location in candidates:
            if platform.system() == "Windows":
                for name in ("activate.bat", "Activate.ps1"):
                    path = location / "Scripts" / name
                    if path.exists():
                        return path
            else:
                path = location / "bin" / "activate"
                if path.exists():
                    return path
        return None
