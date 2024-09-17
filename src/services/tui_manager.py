import subprocess
import os
import platform
import tempfile
import json
import logging

logger = logging.getLogger(__name__)


class TUIManager:
    def __init__(self, run_script_path):
        self.run_script_path = run_script_path

    def open_new_terminal(self, data):
        """
        启动新的终端并运行 TUI 应用，传递数据并获取更新后的结果。
        """
        system = platform.system()
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(self.run_script_path), "..", "..")
        )

        # 序列化数据到临时文件
        with tempfile.NamedTemporaryFile(
            mode="w+", delete=False, suffix=".json"
        ) as tmpfile:
            json.dump(data, tmpfile, ensure_ascii=False, indent=4)
            tmpfile_path = tmpfile.name
            logger.info(f"Temporary file created at: {tmpfile_path}")

        # 查找虚拟环境的激活脚本
        venv_activate = self.find_virtualenv_activate(project_root)

        # 构建运行命令，确保在 project_root 目录下运行并激活虚拟环境
        command = f"python {self.run_script_path} {tmpfile_path}"
        full_command = ""

        if system == "Windows":
            if venv_activate:
                # 使用 '&&' 连接激活虚拟环境和运行命令
                full_command = (
                    f'cmd /c "cd /d "{project_root}" && "{venv_activate}" && {command}"'
                )
            else:
                # 如果未找到虚拟环境，直接运行命令
                full_command = f'cmd /c "cd /d "{project_root}" && {command}"'
        elif system == "Darwin":  # macOS
            if venv_activate:
                # 使用 AppleScript 激活虚拟环境并运行命令
                apple_script = f"""
                tell application "Terminal"
                    do script "source \\"{venv_activate}\\" && cd \\"{project_root}\\" && {command}"
                    activate
                end tell
                """
            else:
                # 如果未找到虚拟环境，直接运行命令
                apple_script = f"""
                tell application "Terminal"
                    do script "cd \\"{project_root}\\" && {command}"
                    activate
                end tell
                """
            full_command = ["osascript", "-e", apple_script]
        elif system == "Linux":
            if venv_activate:
                # 使用 bash 启动新终端，激活虚拟环境并运行命令
                full_command = [
                    "x-terminal-emulator",
                    "-e",
                    f'bash -c \'source "{venv_activate}" && cd "{project_root}" && {command}; exec bash\'',
                ]
            else:
                # 如果未找到虚拟环境，直接运行命令
                full_command = [
                    "x-terminal-emulator",
                    "-e",
                    f"bash -c 'cd \"{project_root}\" && {command}; exec bash'",
                ]
        else:
            raise OSError(f"Unsupported operating system: {system}")

        # 启动新的终端
        try:
            if system == "Darwin":
                subprocess.run(full_command, check=True)
            else:
                subprocess.Popen(full_command, shell=(system == "Windows"))
            logger.info("TUI launched successfully.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to launch TUI: {e}")
            raise

        # 等待用户完成 TUI 操作
        # 这里简化处理，等待用户按回车继续
        input("请在 TUI 完成后按回车继续...")

        # 读取更新后的数据
        try:
            with open(tmpfile_path, "r", encoding="utf-8") as f:
                updated_data = json.load(f)
            logger.info("Updated data retrieved from TUI.")
            return updated_data
        except Exception as e:
            logger.error(f"Failed to read updated data: {e}")
            raise
        finally:
            try:
                os.remove(tmpfile_path)
                logger.info("Temporary file removed.")
            except OSError as e:
                logger.warning(f"Failed to remove temporary file: {e}")

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
