"""ASR 后端抽象与 FunASR Python SDK 实现。

后端只产出带时间戳的识别片段（AsrCue），不碰 SRT 写出。
FunasrAsrBackend 通过 ``funasr.AutoModel`` 一次调用完成 VAD 分段 + 识别 + 标点恢复，
直接返回带时间戳的 ``sentence_info``，无需独立 VAD 二进制、无需段数配对。

相比旧的 llama.cpp 二进制方案（ADR 0002），Python SDK 方案的优势：
- ``language`` 参数约束语言检测，从源头杜绝跨语言幻觉（英语视频不再冒出中文词）；
- ``punc_model`` 恢复标点，解决长段无标点无法断句的问题；
- ``sentence_info`` 直接提供带时间戳分段，无需字数比例投影（方案 E）。
代价是重新引入 PyTorch 依赖；详见 docs/adr/0003。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from subtitle_llm.progress_events import ProgressEmitter
from subtitle_llm.settings import ASRConfig

logger = logging.getLogger(__name__)

# SenseVoice 输出的语言/事件标签，例如 <|en|>、<|EMO_UNKNOWN|>、<|nospeech|>
_SENSEVOICE_TAG_PATTERN = re.compile(r"<\|[^|]*\|>")


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
        model = self._get_or_load_model(AutoModel)

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
        )
        emit_asr_progress(progress, "load_asr", "加载 ASR", "识别完成", status="done")

        if not result:
            logger.warning("FunASR 返回空结果: audio=%s", audio_path)
            return []

        res = result[0]
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
