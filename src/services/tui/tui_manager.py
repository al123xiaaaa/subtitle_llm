import subprocess
import os
import platform
import tempfile
import json
import logging
import time
import shlex

logger = logging.getLogger(__name__)


class TUIManager:
    def __init__(self, run_script_path, width=300, height=52):
        self.run_script_path = run_script_path
        self.width = width
        self.height = height

    def open_new_terminal(self, data):
        """
        启动新的终端并运行 TUI 应用，传递数据并获取更新后的结果。
        """
        system = platform.system()
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(self.run_script_path), "..", "..")
        )

        tui_input_data = {
            "subtitle_entries": data,
            "tui_completed": False,
            "merge_map": [],  # 初始化 merge_map
        }

        # 序列化数据到临时文件
        with tempfile.NamedTemporaryFile(
            mode="w+", delete=False, suffix=".json"
        ) as tmpfile:
            json.dump(tui_input_data, tmpfile, ensure_ascii=False, indent=4)
            tmpfile_path = tmpfile.name
            logger.info(f"Temporary file created at: {tmpfile_path}")

        # 查找虚拟环境的激活脚本
        venv_activate = self.find_virtualenv_activate(project_root)

        # 构建运行命令，确保在 project_root 目录下运行并激活虚拟环境
        command = f"python {self.run_script_path} {tmpfile_path}"
        full_command = ""

        # 下面是根据不同操作系统构建命令，单引号不可变更，因为它们用于包裹整个命令字符串。
        # 在Windows中，单引号用于确保整个命令被正确传递给cmd。
        # 在macOS中，单引号用于AppleScript中的字符串定界。
        # 在Linux中，单引号用于确保命令中的特殊字符不被shell解释。
        # 更改这些单引号可能会导致命令解析错误或执行失败。
        if system == "Windows":
            if venv_activate:
                full_command = f'start cmd /k "mode con: cols={self.width} lines={self.height} && cd /d "{project_root}" && "{venv_activate}" && {command}"'
            else:
                full_command = f'start cmd /k "mode con: cols={self.width} lines={self.height} && cd /d "{project_root}" && {command}"'
        elif system == "Darwin":  # macOS
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
            full_command = ["osascript", "-e", apple_script]
        elif system == "Linux":
            terminal_command = (
                f"x-terminal-emulator -geometry {self.width}x{self.height}"
            )
            if venv_activate:
                full_command = [
                    "bash",
                    "-c",
                    f"{terminal_command} -e 'bash -c \"source {shlex.quote(venv_activate)} && cd {shlex.quote(project_root)} && {command}; exec bash\"'",
                ]
            else:
                full_command = [
                    "bash",
                    "-c",
                    f"{terminal_command} -e 'bash -c \"cd {shlex.quote(project_root)} && {command}; exec bash\"'",
                ]
        else:
            raise OSError(f"Unsupported operating system: {system}")

        # 启动新的终端
        try:
            if system == "Windows":
                subprocess.run(full_command, shell=True, check=True)
            elif system == "Darwin":
                subprocess.run(full_command, check=True)
            else:
                subprocess.Popen(full_command, shell=False)
            logger.info("TUI launched successfully.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to launch TUI: {e}")
            raise

        # 等待 TUI 应用完成并更新临时文件
        while True:
            try:
                with open(tmpfile_path, "r", encoding="utf-8") as f:
                    updated_data = json.load(f)
                if "tui_completed" in updated_data and updated_data["tui_completed"]:
                    logger.info("TUI completed. Retrieving updated data.")
                    return updated_data
            except json.JSONDecodeError:
                # 文件可能正在被写入，等待一段时间后重试
                time.sleep(0.5)
            except FileNotFoundError:
                logger.error(
                    "Temporary file not found. TUI may have encountered an error."
                )
                return None

    def find_virtualenv_activate(self, project_root):
        """
        查找虚拟环境的激活脚本路径。如果找到，则返回路径；否则返回 None。
        """
        possible_venv_locations = [
            os.path.join(project_root, "venv"),
            os.path.join(project_root, ".venv"),
            os.path.expanduser(
                "~/.virtualenvs/subtitle_llm"
            ),  # 如果使用 virtualenvwrapper
        ]

        for location in possible_venv_locations:
            if platform.system() == "Windows":
                activate_script = os.path.join(location, "Scripts", "activate.bat")
                activate_ps1 = os.path.join(location, "Scripts", "Activate.ps1")
                if os.path.exists(activate_script):
                    return activate_script
                elif os.path.exists(activate_ps1):
                    return activate_ps1
            else:
                activate_script = os.path.join(location, "bin", "activate")
                if os.path.exists(activate_script):
                    return activate_script
        logger.warning("Virtual environment activate script not found.")
        return None
