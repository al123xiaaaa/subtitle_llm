"""通过本地隐藏输入保存 Gateway 密钥，不在命令参数或日志中传递密钥。"""

import getpass
import os
from pathlib import Path
import re
import subprocess
import sys


def main() -> None:
    if "--dialog" in sys.argv:
        result = subprocess.run(
            ["osascript", "-e", 'text returned of (display dialog "请输入 AI Gateway API Key；仅保存到本项目 .env.local，不会回传聊天。" default answer "" with hidden answer buttons {"取消", "保存"} default button "保存" with title "Subtitle LLM · Gateway 配置")'],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise SystemExit("配置已取消，未写入密钥。")
        key = result.stdout.strip()
    else:
        if not sys.stdin.isatty():
            raise SystemExit("请在本地交互终端运行此脚本。")
        key = getpass.getpass("AI Gateway API Key（隐藏输入）：").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise SystemExit("密钥为空或含无效字符，未写入。")
    path = Path(__file__).resolve().parents[1] / ".env.local"
    if path.is_symlink():
        raise SystemExit("拒绝写入符号链接。")
    # 保留其他本地配置，只替换目标变量；文件内容不进入日志。
    previous = path.read_text() if path.exists() else ""
    lines = [line for line in previous.splitlines() if not re.match(r"\s*(?:export\s+)?AI_GATEWAY_API_KEY\s*=", line)]
    lines.append(f"AI_GATEWAY_API_KEY={key}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write("\n".join(lines) + "\n")
    print("Gateway 密钥已保存到 .env.local（权限 600，内容不显示）。")


if __name__ == "__main__":
    main()
