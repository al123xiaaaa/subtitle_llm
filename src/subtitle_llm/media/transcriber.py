"""ASR 转写：通过 FunASR Python SDK 把音频转成 SRT 字幕。

转写流程：
1. ASR 后端产出带时间戳的识别片段（AsrCue）
2. 按时间戳组装时间轴字幕（SubtitleEntry）
3. 写出 source-only SRT

语言映射、标签清洗、进度事件与旧实现保持一致；后端细节由 asr_backend 封装。
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.io import SubtitleIO, seconds_to_srt_time
from subtitle_llm.media.asr_backend import AsrBackend, AsrCue, FunasrAsrBackend
from subtitle_llm.progress_events import ProgressEmitter
from subtitle_llm.settings import ASRConfig

# 语言别名 -> 标准名称
ASR_LANGUAGE_ALIASES: Final[dict[str, str | None]] = {
    "auto": None,
    "auto-detect": None,
    "detect": None,
    "none": None,
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "zh-hans": "Chinese",
    "zh-tw": "Chinese",
    "zh-hant": "Chinese",
    "zho": "Chinese",
    "chi": "Chinese",
    "cn": "Chinese",
    "chinese": "Chinese",
    "mandarin": "Chinese",
    "中文": "Chinese",
    "普通话": "Chinese",
    "yue": "Cantonese",
    "zh-hk": "Cantonese",
    "cantonese": "Cantonese",
    "粤语": "Cantonese",
    "en": "English",
    "en-us": "English",
    "en-gb": "English",
    "eng": "English",
    "english": "English",
    "英文": "English",
    "ar": "Arabic",
    "ara": "Arabic",
    "arabic": "Arabic",
    "de": "German",
    "deu": "German",
    "ger": "German",
    "german": "German",
    "fr": "French",
    "fra": "French",
    "fre": "French",
    "french": "French",
    "es": "Spanish",
    "spa": "Spanish",
    "spanish": "Spanish",
    "pt": "Portuguese",
    "por": "Portuguese",
    "portuguese": "Portuguese",
    "id": "Indonesian",
    "ind": "Indonesian",
    "indonesian": "Indonesian",
    "it": "Italian",
    "ita": "Italian",
    "italian": "Italian",
    "ko": "Korean",
    "kor": "Korean",
    "korean": "Korean",
    "ru": "Russian",
    "rus": "Russian",
    "russian": "Russian",
    "th": "Thai",
    "tha": "Thai",
    "thai": "Thai",
    "vi": "Vietnamese",
    "vie": "Vietnamese",
    "vietnamese": "Vietnamese",
    "ja": "Japanese",
    "jpn": "Japanese",
    "japanese": "Japanese",
    "tr": "Turkish",
    "tur": "Turkish",
    "turkish": "Turkish",
    "hi": "Hindi",
    "hin": "Hindi",
    "hindi": "Hindi",
    "ms": "Malay",
    "msa": "Malay",
    "may": "Malay",
    "malay": "Malay",
    "nl": "Dutch",
    "nld": "Dutch",
    "dut": "Dutch",
    "dutch": "Dutch",
    "sv": "Swedish",
    "swe": "Swedish",
    "swedish": "Swedish",
    "da": "Danish",
    "dan": "Danish",
    "danish": "Danish",
    "fi": "Finnish",
    "fin": "Finnish",
    "finnish": "Finnish",
    "pl": "Polish",
    "pol": "Polish",
    "polish": "Polish",
    "cs": "Czech",
    "ces": "Czech",
    "cze": "Czech",
    "czech": "Czech",
    "fil": "Filipino",
    "tl": "Filipino",
    "tagalog": "Filipino",
    "filipino": "Filipino",
    "fa": "Persian",
    "fas": "Persian",
    "per": "Persian",
    "persian": "Persian",
    "el": "Greek",
    "ell": "Greek",
    "gre": "Greek",
    "greek": "Greek",
    "ro": "Romanian",
    "ron": "Romanian",
    "rum": "Romanian",
    "romanian": "Romanian",
    "hu": "Hungarian",
    "hun": "Hungarian",
    "hungarian": "Hungarian",
    "mk": "Macedonian",
    "mkd": "Macedonian",
    "mac": "Macedonian",
    "macedonian": "Macedonian",
}


def normalize_asr_language(language: str | None) -> str | None:
    """将用户输入的语言标识标准化为可读名称（如 'en' -> 'English'）。"""
    if language is None:
        return None

    raw_language = language.strip()
    if not raw_language:
        return None

    normalized_key = raw_language.lower().replace("_", "-")
    if normalized_key in ASR_LANGUAGE_ALIASES:
        return ASR_LANGUAGE_ALIASES[normalized_key]

    base_alias = ASR_LANGUAGE_ALIASES.get(normalized_key.split("-", maxsplit=1)[0])
    if base_alias is not None:
        return base_alias

    return raw_language


def transcribe(
    audio_path: str | Path,
    language: str | None,
    output_path: str | Path,
    config: ASRConfig | None = None,
    progress: ProgressEmitter | None = None,
) -> str:
    """使用 FunASR Python SDK 将音频转写为 SRT 字幕文件。

    策略：ASR 后端产出带时间戳的识别片段，按时间戳组装时间轴字幕。
    """
    config = config or ASRConfig()
    backend = FunasrAsrBackend(config=config)
    return transcribe_with_backend(audio_path, language, output_path, backend, progress=progress)


def transcribe_with_backend(
    audio_path: str | Path,
    language: str | None,
    output_path: str | Path,
    backend: AsrBackend,
    progress: ProgressEmitter | None = None,
) -> str:
    """用指定 ASR 后端把音频转写成 SRT。后端可注入，便于测试与替换。"""
    asr_language = normalize_asr_language(language)
    emit_progress(
        progress,
        "normalize_language",
        "识别语言",
        f"ASR 语言：{'自动识别' if asr_language is None else asr_language}",
        status="done",
    )

    if asr_language != language:
        display_language = "自动识别" if asr_language is None else asr_language
        print(f"ASR 语言：{display_language}（来自 {language}）")

    emit_progress(progress, "transcribe_audio", "转写音频", f"正在转写：{Path(audio_path).name}")
    print(f"正在转写：{Path(audio_path).name}...")
    cues = backend.transcribe(audio_path, asr_language, progress=progress)
    emit_progress(progress, "transcribe_audio", "转写音频", "音频转写完成", status="done")

    subtitle = build_subtitle(cues)

    emit_progress(progress, "write_srt", "写出字幕", f"正在写出字幕：{output_path}")
    SubtitleIO.write_srt(subtitle, output_path, output_format="source-only")
    emit_progress(progress, "write_srt", "写出字幕", f"字幕已生成：{output_path}", status="done")
    print(f"字幕已生成：{output_path}（{len(subtitle.entries)} 条）")
    return str(output_path)


def build_subtitle(cues: list[AsrCue]) -> Subtitle:
    """把识别片段组装成时间轴字幕。每条片段保证非空文本且最小时长 100ms。"""
    subtitle = Subtitle()
    for cue in cues:
        start_sec = cue.start_ms / 1000.0
        end_sec = max(cue.end_ms, cue.start_ms + 100) / 1000.0
        subtitle.add_entry(
            SubtitleEntry(
                index=len(subtitle.entries) + 1,
                start_time=seconds_to_srt_time(start_sec),
                end_time=seconds_to_srt_time(end_sec),
                original_text=cue.text,
            )
        )
    return subtitle


def emit_progress(
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
