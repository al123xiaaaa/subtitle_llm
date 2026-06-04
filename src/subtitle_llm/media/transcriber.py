"""ASR transcription using FunASR (SenseVoiceSmall) with VAD-based segmentation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.io import SubtitleIO, seconds_to_srt_time
from subtitle_llm.settings import ASRConfig

# FunASR SenseVoiceSmall 支持的语言代码映射
# 参考: https://github.com/modelscope/FunASR
FUNASR_LANGUAGE_MAP: Final[dict[str, str]] = {
    "chinese": "zh",
    "english": "en",
    "cantonese": "yue",
    "japanese": "ja",
    "korean": "ko",
    "arabic": "ar",
    "german": "de",
    "french": "fr",
    "spanish": "es",
    "portuguese": "pt",
    "indonesian": "id",
    "italian": "it",
    "russian": "ru",
    "thai": "th",
    "vietnamese": "vi",
    "turkish": "tr",
    "hindi": "hi",
    "malay": "ms",
    "dutch": "nl",
    "swedish": "sv",
    "danish": "da",
    "finnish": "fi",
    "polish": "pl",
    "czech": "cs",
    "filipino": "fil",
    "persian": "fa",
    "greek": "el",
    "romanian": "ro",
    "hungarian": "hu",
    "macedonian": "mk",
}

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

CJK_ASR_LANGUAGES: Final[set[str]] = {"Chinese", "Japanese", "Cantonese"}


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

    title_language = " ".join(
        part.capitalize()
        for part in raw_language.replace("-", " ").replace("_", " ").split()
    )
    if title_language in FUNASR_LANGUAGE_MAP:
        return title_language

    return raw_language


def _to_funasr_language(language: str | None) -> str | None:
    """将标准化语言名称转为 FunASR 语言代码（如 'English' -> 'en'）。auto 则返回 None。"""
    if language is None:
        return None
    return FUNASR_LANGUAGE_MAP.get(language.lower())


def _clean_funasr_text(text: str) -> str:
    """清除 FunASR 输出中的特殊标记（如 <|zh|>、<|EMO_UNKNOWN|> 等情感/事件标签）。"""
    text = re.sub(r"<\|[^|]*\|>", "", text)
    return text.strip()


def transcribe(
    audio_path: str | Path,
    language: str | None,
    output_path: str | Path,
    config: ASRConfig | None = None,
) -> str:
    """使用 FunASR SenseVoiceSmall 将音频转写为 SRT 字幕文件。

    策略：
    1. 先用 VAD 模型单独获取语音片段时间戳
    2. 用 ASR+VAD 组合调用获取完整文本（VAD 分片 + 每片独立 ASR）
    3. 按 <|lang|> 标签分割文本，与 VAD 片段一一对应
    """
    from funasr import AutoModel

    config = config or ASRConfig()
    asr_language = normalize_asr_language(language)

    if asr_language != language:
        display_language = "自动识别" if asr_language is None else asr_language
        print(f"ASR 语言：{display_language}（来自 {language}）")

    audio_path = Path(audio_path)
    device = config.device or "cpu"

    # Step 1: VAD 获取语音片段时间戳
    print("正在运行 VAD 检测语音片段...")
    vad_model = AutoModel(
        model=config.vad_model, device=device, disable_update=True
    )
    vad_result = vad_model.generate(input=str(audio_path), batch_size=1)
    vad_segments = vad_result[0]["value"]  # [[start_ms, end_ms], ...]
    print(f"VAD 检测到 {len(vad_segments)} 个语音片段")

    # Step 2: ASR + VAD 组合调用获取文本
    # SenseVoiceSmall + VAD 会对每个 VAD 片段独立转写，
    # 输出合并为一条文本，每个片段以 <|lang|> 标签开头
    print(f"正在加载 FunASR 模型 ({config.model})...")
    asr_model = AutoModel(
        model=config.model,
        vad_model=config.vad_model,
        vad_kwargs={"max_single_segment_time": config.vad_max_segment_ms},
        device=device,
        disable_update=True,
    )

    print(f"正在转写：{audio_path.name}...")
    funasr_lang = _to_funasr_language(asr_language)
    generate_kwargs: dict[str, Any] = {"batch_size": 1}
    if funasr_lang:
        generate_kwargs["language"] = funasr_lang

    asr_result = asr_model.generate(input=str(audio_path), **generate_kwargs)
    raw_text = asr_result[0].get("text", "")

    # Step 3: 按 <|lang|> 标签分割文本
    # SenseVoiceSmall 输出格式: <|en|><|EMO_UNKNOWN|><|Speech|><|woitn|>text ...
    # 每个 VAD 片段对应一组标签+文本
    segments = re.split(
        r"(?=<\|(?:en|zh|ja|ko|yue|ar|de|fr|es|pt|id|it|ru|th|vi|tr|hi|ms|nl|sv|da|fi|pl|cs|fil|fa|el|ro|hu|mk)\|>)",
        raw_text,
    )
    segments = [s.strip() for s in segments if s.strip()]

    # Step 4: 生成字幕条目
    subtitle = Subtitle()

    if len(segments) == len(vad_segments):
        # 完美匹配：每个文本片段对应一个 VAD 时间段
        for seg_text, (start_ms, end_ms) in zip(segments, vad_segments):
            clean = _clean_funasr_text(seg_text)
            if not clean:
                continue

            start_sec = start_ms / 1000.0
            end_sec = end_ms / 1000.0

            subtitle.add_entry(
                SubtitleEntry(
                    index=len(subtitle.entries) + 1,
                    start_time=seconds_to_srt_time(start_sec),
                    end_time=seconds_to_srt_time(max(end_sec, start_sec + 0.1)),
                    original_text=clean,
                )
            )
    else:
        # 数量不匹配：降级处理
        print(
            f"⚠️ VAD({len(vad_segments)})与文本({len(segments)})数量不匹配，使用降级方案"
        )
        full_clean = _clean_funasr_text(raw_text)
        if full_clean:
            subtitle.add_entry(
                SubtitleEntry(
                    index=1,
                    start_time=seconds_to_srt_time(0),
                    end_time=seconds_to_srt_time(0),
                    original_text=full_clean,
                )
            )

    SubtitleIO.write_srt(subtitle, output_path, output_format="source-only")
    print(f"字幕已生成：{output_path}（{len(subtitle.entries)} 条）")
    return str(output_path)
