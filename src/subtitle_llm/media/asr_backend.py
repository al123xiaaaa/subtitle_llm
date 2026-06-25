"""ASR 后端抽象与 FunASR Python SDK 实现。

后端只产出带时间戳的识别片段（AsrCue），不碰 SRT 写出。
FunasrAsrBackend 通过 ``funasr.AutoModel`` 一次调用完成 VAD 分段 + 识别 + 标点恢复，
优先用顶层 ``words`` + ``timestamp`` 重建时间轴，避免 ``sentence_info`` 句级漂移。

相比旧的 llama.cpp 二进制方案（ADR 0002），Python SDK 方案的优势：
- ``language`` 参数约束语言检测，从源头杜绝跨语言幻觉（英语视频不再冒出中文词）；
- ``punc_model`` 恢复标点，解决长段无标点无法断句的问题；
- 顶层词级 timestamp 提供原始音频时间轴，无需字数比例投影（方案 E）。
代价是重新引入 PyTorch 依赖；详见 docs/adr/0003。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from subtitle_llm.progress_events import ProgressEmitter
from subtitle_llm.settings import ASRConfig

logger = logging.getLogger(__name__)

# SenseVoice 输出的语言/事件标签，例如 <|en|>、<|EMO_UNKNOWN|>、<|nospeech|>
_SENSEVOICE_TAG_PATTERN = re.compile(r"<\|[^|]*\|>")
_SENTENCE_END_TOKENS = {".", "!", "?", "。", "！", "？"}
_NO_SPACE_BEFORE = {
    ".",
    ",",
    "!",
    "?",
    ";",
    ":",
    "%",
    "，",
    "。",
    "！",
    "？",
    "；",
    "：",
    "、",
    ")",
    "]",
    "}",
    "）",
    "】",
    "》",
    "」",
    "』",
}
_NO_SPACE_AFTER = {"(", "[", "{", "（", "【", "《", "「", "『"}


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
    return _SENSEVOICE_TAG_PATTERN.sub("", text).replace("▁", "").strip()


def _build_cues_from_word_timestamps(words: object, timestamps: object) -> list[AsrCue]:
    """从 FunASR 顶层 words/timestamp 重建字幕，避开 sentence_info 句级时间漂移。"""
    if not isinstance(words, list) or not isinstance(timestamps, list):
        return []
    if not words or len(words) != len(timestamps):
        return []

    tokens: list[tuple[str, int, int]] = []
    for word, timestamp in zip(words, timestamps, strict=True):
        text = clean_sensevoice_text(str(word)).strip()
        parsed_timestamp = _parse_word_timestamp(timestamp)
        if not text or parsed_timestamp is None:
            continue
        start_ms, end_ms = parsed_timestamp
        if end_ms < start_ms:
            continue
        tokens.append((text, start_ms, max(end_ms, start_ms + 1)))

    if not tokens:
        return []

    cues: list[AsrCue] = []
    current: list[tuple[str, int, int]] = []
    for index, token in enumerate(tokens):
        current.append(token)
        previous_text = tokens[index - 1][0] if index > 0 else None
        next_text = tokens[index + 1][0] if index + 1 < len(tokens) else None
        if _is_sentence_boundary_token(token[0], previous_text=previous_text, next_text=next_text):
            _append_word_timestamp_cue(cues, current)
            current = []
    _append_word_timestamp_cue(cues, current)
    return cues


def _parse_word_timestamp(timestamp: object) -> tuple[int, int] | None:
    if isinstance(timestamp, dict):
        start = timestamp.get("start_time", timestamp.get("start"))
        end = timestamp.get("end_time", timestamp.get("end"))
        if start is None or end is None:
            return None
        return int(float(start) * 1000), int(float(end) * 1000)
    if isinstance(timestamp, (list, tuple)) and len(timestamp) >= 2:
        return int(float(timestamp[0])), int(float(timestamp[1]))
    return None


def _append_word_timestamp_cue(cues: list[AsrCue], tokens: list[tuple[str, int, int]]) -> None:
    if not tokens:
        return
    text = _format_word_tokens([token for token, _, _ in tokens])
    text = clean_sensevoice_text(text).strip()
    if not text:
        return
    cues.append(AsrCue(start_ms=tokens[0][1], end_ms=tokens[-1][2], text=text))


def _format_word_tokens(tokens: list[str]) -> str:
    text = ""
    for index, token in enumerate(tokens):
        previous_token = tokens[index - 1] if index > 0 else None
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        if not text:
            text = token
        elif _joins_previous_token(token, previous_token=previous_token, next_token=next_token, current_text=text):
            text += token
        else:
            text += " " + token
    return text


def _joins_previous_token(
    token: str,
    *,
    previous_token: str | None,
    next_token: str | None,
    current_text: str,
) -> bool:
    if token == "." and previous_token and next_token and previous_token.isdigit() and next_token.isdigit():
        return True
    if token.isdigit() and previous_token and previous_token.isdigit():
        return True
    if previous_token == "." and token.isdigit() and len(current_text) >= 2 and current_text[-2].isdigit():
        return True
    if current_text[-1:] in {"'", "’"}:
        return True
    if token in _NO_SPACE_BEFORE:
        return True
    if token.startswith(("'", "’")):
        return True
    return current_text[-1:] in _NO_SPACE_AFTER


def _is_sentence_boundary_token(
    token: str,
    *,
    previous_text: str | None,
    next_text: str | None,
) -> bool:
    if token == "." and previous_text and next_text and previous_text.isdigit() and next_text.isdigit():
        return False
    if token in _SENTENCE_END_TOKENS:
        return True
    return token[-1:] in _SENTENCE_END_TOKENS


@dataclass
class FunasrAsrBackend:
    """基于 FunASR Python SDK 的 ASR 后端。

    通过 ``funasr.AutoModel`` 组合 SenseVoice（识别）+ fsmn-vad（分段）+
    ct-punc（标点恢复）+ cam++（说话人分离，触发 sentence_info 输出），
    一次调用产出带时间戳、带标点、语言受约束的识别片段。

    配置见 ASRConfig（模型名、max_single_segment_time、device 等）。
    首次运行会自动从 ModelScope/HuggingFace 下载模型（约 1GB）。
    """

    config: ASRConfig
    _model: object | None = field(default=None, init=False, repr=False)

    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None,
        progress: ProgressEmitter | None = None,
    ) -> list[AsrCue]:
        from funasr import AutoModel
        from funasr.utils.postprocess_utils import rich_transcription_postprocess

        emit_asr_progress(progress, "load_asr", "加载 ASR", "正在加载 FunASR 模型")
        model = cast(Any, self._get_or_load_model(AutoModel))

        emit_asr_progress(progress, "load_asr", "加载 ASR", "正在运行 FunASR 识别")
        # language: 用户指定（如 "English"）则约束识别语言，杜绝跨语言幻觉；
        # None/"auto" 时由模型自动检测。
        lang = self._normalize_language(language)
        result = model.generate(
            input=str(audio_path),
            cache={},
            language=lang,
            use_itn=True,
            batch_size_s=60,
            output_timestamp=True,
        )
        emit_asr_progress(progress, "load_asr", "加载 ASR", "识别完成", status="done")

        if not result:
            logger.warning("FunASR 返回空结果: audio=%s", audio_path)
            return []

        res = result[0]
        word_timestamp_cues = _build_cues_from_word_timestamps(
            res.get("words", []),
            res.get("timestamp", []),
        )
        if word_timestamp_cues:
            logger.info(
                "FunASR 词级时间戳重建出 %s 个片段: audio=%s",
                len(word_timestamp_cues),
                audio_path,
            )
            return word_timestamp_cues

        sentence_info = res.get("sentence_info", [])
        cues: list[AsrCue] = []

        if sentence_info:
            # 正常路径：sentence_info 提供带时间戳的分段（需 spk_model 触发）
            for seg in sentence_info:
                raw_text = seg.get("text", seg.get("sentence", ""))
                text = rich_transcription_postprocess(raw_text)
                text = clean_sensevoice_text(text)
                if not text:
                    continue
                cues.append(AsrCue(
                    start_ms=int(seg.get("start", 0)),
                    end_ms=int(seg.get("end", 0)),
                    text=text,
                ))
            logger.info(
                "FunASR 识别出 %s 个片段: audio=%s", len(cues), audio_path
            )
        else:
            # 降级：模型未返回 sentence_info（通常未配 spk_model），整段一条。
            logger.warning(
                "FunASR 未返回 sentence_info，降级为整段一条: audio=%s", audio_path
            )
            text = clean_sensevoice_text(
                rich_transcription_postprocess(res.get("text", ""))
            )
            if text:
                # 无时间戳信息，用 0 占位（build_subtitle 会处理）
                cues.append(AsrCue(start_ms=0, end_ms=0, text=text))

        return cues

    def _get_or_load_model(self, auto_model_cls) -> object:
        """懒加载并缓存 AutoModel 实例（模型加载耗时，避免每次转写都重建）。"""
        if self._model is not None:
            return self._model

        cfg = self.config
        kwargs: dict[str, object] = {
            "model": cfg.model_name,
            "vad_model": "fsmn-vad",
            "vad_kwargs": {"max_single_segment_time": cfg.max_single_segment_time},
            "punc_model": cfg.punc_model,
            "spk_model": cfg.spk_model,
            "device": cfg.device,
            "disable_update": True,
            "disable_pbar": True,
        }
        # Fun-ASR-Nano / Qwen3-ASR 等需 trust_remote_code + HF hub
        if cfg.hub:
            kwargs["hub"] = cfg.hub
        if cfg.trust_remote_code:
            kwargs["trust_remote_code"] = True

        logger.info(
            "加载 FunASR 模型: model=%s punc=%s spk=%s device=%s",
            cfg.model_name, cfg.punc_model, cfg.spk_model, cfg.device,
        )
        self._model = auto_model_cls(**kwargs)
        return self._model

    @staticmethod
    def _normalize_language(language: str | None) -> str:
        """把 ASR 语言标识（如 "English"）转为 funasr 接受的形式（如 "en"）。

        funasr 的 language 参数接受语言代码或 "auto"。
        用户传入的可能是完整名称（来自 normalize_asr_language），这里映射回代码。
        """
        if not language:
            return "auto"
        mapping = {
            "english": "en", "chinese": "zh", "japanese": "ja",
            "korean": "ko", "cantonese": "yue", "auto": "auto",
        }
        return mapping.get(language.strip().lower(), language.strip().lower())


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
