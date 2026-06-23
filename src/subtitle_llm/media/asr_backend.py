"""ASR 后端抽象与 FunASR llama.cpp / GGUF runtime 实现。

后端只产出带时间戳的识别片段（AsrCue），不碰 SRT 写出。
LlamacppAsrBackend 通过两个独立二进制协同工作：
- llama-funasr-vad：输出每段语音的 ``start_ms end_ms``（每段一行）
- llama-funasr-sensevoice --vad：输出带 ``<|lang|>`` 标签的识别文本，段数与 VAD 一致

两个二进制都基于同一个 fsmn-vad.gguf，因此段数稳定一致（实测在 5 分钟 / 82 段
规模下两次运行完全相同）。文本与时间戳按下标 1:1 配对，不需要赌正则切分数。
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from subtitle_llm.progress_events import ProgressEmitter
from subtitle_llm.settings import ASRConfig

logger = logging.getLogger(__name__)

# SenseVoice 输出的语言/事件标签，例如 <|en|>、<|EMO_UNKNOWN|>、<|nospeech|>
_SENSEVOICE_TAG_PATTERN = re.compile(r"<\|[^|]*\|>")

# SenseVoice 每个 VAD 片段输出的文本以“段首标签”开头：语言码或 nospeech。
# 注意：事件/情感标签（<|EMO_UNKNOWN|>、<|Speech|>、<|Event_UNK|>、<|woitn|> 等）
# 不是段首，不能用作切分点，否则会把单段错切成多段、破坏与 VAD 时间戳的 1:1 配对。
# 因此这里用显式的段首标签集合，而不是宽泛的 <|[a-z]+|> 正则。
_SEGMENT_START_TAGS = (
    "en", "zh", "ja", "ko", "yue", "ar", "de", "fr", "es", "pt", "id", "it",
    "ru", "th", "vi", "tr", "hi", "ms", "nl", "sv", "da", "fi", "pl", "cs",
    "fil", "fa", "el", "ro", "hu", "mk", "nospeech",
)
_SEGMENT_START_PATTERN = re.compile(
    r"(?=<\|(?:" + "|".join(_SEGMENT_START_TAGS) + r")\|>)"
)


class AsrError(RuntimeError):
    """ASR 后端调用或解析失败。"""


@dataclass(frozen=True)
class AsrCue:
    """一段带时间戳的识别结果。"""

    start_ms: int
    end_ms: int
    text: str


class AsrBackend(Protocol):
    """ASR 后端协议：把音频转写成带时间戳的识别片段。"""

    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None,
        progress: ProgressEmitter | None = None,
    ) -> list[AsrCue]:
        ...


def clean_sensevoice_text(text: str) -> str:
    """清除 SenseVoice 输出中的特殊标记（如 <|en|>、<|EMO_UNKNOWN|>、<|nospeech|>）。"""
    return _SENSEVOICE_TAG_PATTERN.sub("", text).strip()


@dataclass
class LlamacppAsrBackend:
    """基于 FunASR llama.cpp / GGUF runtime 二进制的 ASR 后端。

    依赖两个预编译二进制和对应的 GGUF 权重（见 default.yaml 的 asr 段）。
    无需 Python、无需 PyTorch，CPU 上运行；自带 FSMN-VAD。
    """

    config: ASRConfig

    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None,
        progress: ProgressEmitter | None = None,
    ) -> list[AsrCue]:
        emit_asr_progress(progress, "vad", "VAD 检测", "正在检测语音片段")
        vad_segments = self._run_vad(audio_path)
        emit_asr_progress(
            progress, "vad", "VAD 检测", f"检测到 {len(vad_segments)} 个语音片段", status="done"
        )
        logger.info("VAD 检测到 %s 个语音片段: audio=%s", len(vad_segments), audio_path)

        emit_asr_progress(progress, "load_asr", "加载 ASR", "正在运行 FunASR 识别")
        raw_text = self._run_sensevoice(audio_path)
        emit_asr_progress(progress, "load_asr", "加载 ASR", "识别完成", status="done")
        segments = self._split_sensevoice_segments(raw_text)
        logger.info("SenseVoice 识别出 %s 个文本片段: audio=%s", len(segments), audio_path)

        if len(segments) == len(vad_segments):
            cues = [
                AsrCue(start_ms=start, end_ms=end, text=clean_sensevoice_text(text))
                for (start, end), text in zip(vad_segments, segments)
            ]
            return [cue for cue in cues if cue.text]

        # 段数不一致（理论上不会发生，两个二进制同源 VAD）：降级为整段一条。
        logger.warning(
            "VAD(%s)与文本(%s)段数不一致，使用降级方案: audio=%s",
            len(vad_segments),
            len(segments),
            audio_path,
        )
        full_text = clean_sensevoice_text(raw_text)
        if not full_text or not vad_segments:
            return []
        start_ms, end_ms = vad_segments[0][0], vad_segments[-1][1]
        return [AsrCue(start_ms=start_ms, end_ms=end_ms, text=full_text)]

    def _run_vad(self, audio_path: str | Path) -> list[tuple[int, int]]:
        """运行 llama-funasr-vad，返回 [(start_ms, end_ms), ...]。"""
        cmd = [
            self.config.vad_binary,
            "-m",
            str(self.config.model_path(self.config.vad_model)),
            "-a",
            str(audio_path),
        ]
        output = self._run_binary(cmd, label="VAD")
        segments: list[tuple[int, int]] = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                start, end = int(parts[0]), int(parts[1])
            except ValueError:
                logger.warning("VAD 输出无法解析为时间戳，跳过: %r", line)
                continue
            segments.append((start, end))
        return segments

    def _run_sensevoice(self, audio_path: str | Path) -> str:
        """运行 llama-funasr-sensevoice --vad，返回带 <|lang|> 标签的原始文本。"""
        cmd = [
            self.config.sensevoice_binary,
            "-m",
            str(self.config.model_path(self.config.sensevoice_model)),
            "-a",
            str(audio_path),
            "--vad",
            str(self.config.model_path("fsmn-vad.gguf")),
            "--keep-tags",
        ]
        return self._run_binary(cmd, label="SenseVoice").strip()

    def _split_sensevoice_segments(self, raw_text: str) -> list[str]:
        """按语言起始标签把整段文本切成与 VAD 片段对应的子串。

        SenseVoice 对每个 VAD 片段输出一组标签+文本，形如：
        ``<|en|><|EMO_UNKNOWN|><|Speech|><|woitn|>text...<|nospeech|>...``
        每个片段以语言标签（如 <|en|>、<|zh|>、<|nospeech|>）开头。
        """
        segments = _SEGMENT_START_PATTERN.split(raw_text)
        return [s.strip() for s in segments if s.strip()]

    def _run_binary(self, cmd: list[str], *, label: str) -> str:
        """执行二进制，stderr 丢弃（ggml 初始化日志），返回 stdout。"""
        logger.info("运行 %s 二进制: %s", label, " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=self.config.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise AsrError(
                f"{label} 二进制不存在：{cmd[0]}。请在配置中设置正确路径，"
                "或参考 docs/adr/0002 下载 FunASR llama.cpp runtime。"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise AsrError(
                f"{label} 二进制执行失败（退出码 {exc.returncode}）：{exc.stderr.strip()[:500]}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AsrError(f"{label} 二进制执行超时（{self.config.timeout_seconds}s）") from exc
        return result.stdout


def emit_asr_progress(
    progress: ProgressEmitter | None,
    detail: str,
    label: str,
    message: str,
    *,
    status: str = "running",
) -> None:
    if progress is None:
        return
    progress.emit(
        stage="prepare_input" if progress.command == "translate" else "transcribe",
        detail=detail,
        status=status,
        label=label,
        message=message,
    )

