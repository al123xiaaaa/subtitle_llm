"""transcribe.cpp ASR 后端：whisper 系 GGUF 模型走 ggml CLI（Metal/Vulkan/CUDA）。

与 FunasrAsrBackend 实现同一个 AsrBackend 协议。transcribe-cli 输出段级
时间戳（[start -> end] text），正好满足管线的时间轴需求；长音频由库内部
按 30 秒窗口自动拼接，单次调用不限时长。

二进制解析顺序：SUBTITLE_LLM_TRANSCRIBE_CLI 环境变量 → PATH → 常见安装位置
→ 项目内 vendor 目录 → 开发机约定路径。GGUF 模型首次使用自动从
handy-computer 的 HuggingFace 仓库下载到本地缓存。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from subtitle_llm.media.asr_backend import AsrCue
from subtitle_llm.progress_events import ProgressEmitter
from subtitle_llm.settings import ASRConfig

logger = logging.getLogger(__name__)

#   [ 166.84 ->  175.00] text（允许前导空白：CLI 实际输出有缩进）
_SEGMENT_LINE = re.compile(r"^\s*\[\s*(\d+(?:\.\d+)?)\s*->\s*(\d+(?:\.\d+)?)\]\s*(.*)$")

_GGUF_CACHE_DIR = Path.home() / ".cache" / "subtitle-llm" / "gguf"


class TranscribeCppError(RuntimeError):
    """transcribe.cpp 后端环境/运行错误。"""


@dataclass(frozen=True)
class TranscribeCppBackend:
    config: ASRConfig

    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None,
        progress: ProgressEmitter | None = None,
    ) -> list[AsrCue]:
        cli = _resolve_transcribe_cli()
        model = _resolve_gguf_model(self.config, progress)
        lang = normalize_iso_language(language)

        _emit(progress, "正在运行 transcribe-cli 识别")
        cmd = [
            cli,
            "-m", model,
            "-l", lang,
            "--timestamps", "segment",
            "-q",
            str(audio_path),
        ]
        logger.info("transcribe-cli: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise TranscribeCppError(
                f"transcribe-cli 失败（exit {result.returncode}）：{result.stderr.strip()[:500]}"
            )
        cues = parse_segment_output(result.stdout)
        logger.info("transcribe-cli 识别完成: audio=%s cues=%s", audio_path, len(cues))
        _emit(progress, "识别完成", status="done")
        return cues


def parse_segment_output(stdout: str) -> list[AsrCue]:
    """把 transcribe-cli 的段级输出解析成 AsrCue。非段行（日志/realtime 统计）忽略。"""
    cues: list[AsrCue] = []
    for line in stdout.splitlines():
        match = _SEGMENT_LINE.match(line)
        if not match:
            continue
        text = match.group(3).strip()
        if not text:
            continue
        cues.append(
            AsrCue(
                start_ms=round(float(match.group(1)) * 1000),
                end_ms=round(float(match.group(2)) * 1000),
                text=text,
            )
        )
    return cues


def normalize_iso_language(language: str | None) -> str:
    """把语言标识转成 transcribe-cli 接受的 ISO 代码（如 English -> en）。"""
    if not language:
        return "auto"
    normalized = language.strip().lower()
    mapping = {
        "english": "en", "chinese": "zh", "japanese": "ja",
        "korean": "ko", "cantonese": "yue", "french": "fr",
        "german": "de", "spanish": "es", "italian": "it",
        "portuguese": "pt", "russian": "ru", "auto": "auto",
    }
    return mapping.get(normalized, normalized[:2])


def _resolve_transcribe_cli() -> str:
    candidates = [
        os.getenv("SUBTITLE_LLM_TRANSCRIBE_CLI"),
        shutil.which("transcribe-cli"),
        "/opt/homebrew/bin/transcribe-cli",
        "/usr/local/bin/transcribe-cli",
        # 开发机约定：克隆到 ~/Development/Github 的 transcribe.cpp 构建产物
        str(Path.home() / "Development" / "Github" / "transcribe.cpp" / "build" / "bin" / "transcribe-cli"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise TranscribeCppError(
        "未找到 transcribe-cli。安装方式：\n"
        "  git clone https://github.com/handy-computer/transcribe.cpp && cd transcribe.cpp\n"
        "  cmake -B build && cmake --build build\n"
        "然后把 build/bin/transcribe-cli 加入 PATH，或设 SUBTITLE_LLM_TRANSCRIBE_CLI 环境变量。"
    )


def _resolve_gguf_model(config: ASRConfig, progress: ProgressEmitter | None) -> str:
    if not config.gguf_file:
        raise TranscribeCppError("当前 ASR profile 未配置 gguf_file")

    # 显式路径优先（用户可直接指向已有 GGUF 文件）
    explicit = Path(config.gguf_file).expanduser()
    if explicit.exists():
        return str(explicit)

    cached = _GGUF_CACHE_DIR / config.gguf_file
    if cached.exists():
        return str(cached)

    if not config.gguf_repo:
        raise TranscribeCppError(f"GGUF 模型不存在且未配置 gguf_repo：{config.gguf_file}")

    url = f"https://huggingface.co/{config.gguf_repo}/resolve/main/{config.gguf_file}"
    _GGUF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _emit(progress, f"首次使用，正在下载 GGUF 模型：{config.gguf_file}")
    logger.info("下载 GGUF 模型: %s -> %s", url, cached)
    try:
        urllib.request.urlretrieve(url, cached)
    except Exception as exc:
        cached.unlink(missing_ok=True)
        raise TranscribeCppError(f"GGUF 模型下载失败：{url}：{exc}") from exc
    return str(cached)


def _emit(progress: ProgressEmitter | None, message: str, *, status: str = "running") -> None:
    if progress is None:
        return
    progress.emit(
        stage="prepare_input" if progress.command == "translate" else "transcribe",
        detail="load_asr",
        status=status,
        label="加载 ASR",
        message=message,
    )
