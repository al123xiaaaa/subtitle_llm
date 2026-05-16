from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Final, cast

from pydub import AudioSegment

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.io import SubtitleIO, seconds_to_srt_time
from subtitle_llm.settings import ASRConfig


SUPPORTED_ASR_LANGUAGES: Final[set[str]] = {
    "Chinese",
    "English",
    "Cantonese",
    "Arabic",
    "German",
    "French",
    "Spanish",
    "Portuguese",
    "Indonesian",
    "Italian",
    "Korean",
    "Russian",
    "Thai",
    "Vietnamese",
    "Japanese",
    "Turkish",
    "Hindi",
    "Malay",
    "Dutch",
    "Swedish",
    "Danish",
    "Finnish",
    "Polish",
    "Czech",
    "Filipino",
    "Persian",
    "Greek",
    "Romanian",
    "Hungarian",
    "Macedonian",
}

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
    if language is None:
        return None

    raw_language = language.strip()
    if not raw_language:
        return None

    normalized_key = raw_language.lower().replace("_", "-")
    alias = ASR_LANGUAGE_ALIASES.get(normalized_key)
    if normalized_key in ASR_LANGUAGE_ALIASES:
        return alias

    base_alias = ASR_LANGUAGE_ALIASES.get(normalized_key.split("-", maxsplit=1)[0])
    if base_alias is not None:
        return base_alias

    title_language = " ".join(part.capitalize() for part in raw_language.replace("-", " ").replace("_", " ").split())
    if title_language in SUPPORTED_ASR_LANGUAGES:
        return title_language

    return raw_language


def transcribe(
    audio_path: str | Path,
    language: str | None,
    output_path: str | Path,
    config: ASRConfig | None = None,
) -> str:
    qwen_asr: Any = importlib.import_module("qwen_asr")
    Qwen3ASRModel = qwen_asr.Qwen3ASRModel

    config = config or ASRConfig()
    kwargs = {"device_map": config.device} if config.device else {}
    asr_language = normalize_asr_language(language)
    if asr_language != language:
        display_language = "自动识别" if asr_language is None else asr_language
        print(f"ASR 语言：{display_language}（来自 {language}）")

    print(f"正在加载 Qwen3-ASR 模型 ({config.model})...")
    model = Qwen3ASRModel.from_pretrained(
        config.model,
        forced_aligner=config.forced_aligner,
        forced_aligner_kwargs=kwargs,
        **kwargs,
    )

    audio_path = Path(audio_path)
    audio = AudioSegment.from_file(audio_path)
    duration_sec = len(audio) / 1000.0

    if duration_sec <= config.max_audio_length:
        segments = [audio_path]
        offsets = [0.0]
    else:
        print(f"音频时长 {duration_sec:.1f}s，分割为 {config.max_audio_length}s 的片段...")
        segments, offsets = _split_audio(audio, audio_path, config.max_audio_length)

    subtitle = Subtitle()
    entry_index = 1

    for index, (segment_path, offset) in enumerate(zip(segments, offsets), start=1):
        print(f"正在转写片段 {index}/{len(segments)}...")
        results = model.transcribe(audio=[str(segment_path)], language=[asr_language], return_time_stamps=True)
        for result in results:
            if not result.time_stamps:
                continue
            for timestamp in result.time_stamps:
                subtitle.add_entry(
                    SubtitleEntry(
                        index=entry_index,
                        start_time=seconds_to_srt_time(timestamp.start_time + offset),
                        end_time=seconds_to_srt_time(timestamp.end_time + offset),
                        original_text=timestamp.text.strip(),
                    )
                )
                entry_index += 1

        if segment_path != audio_path:
            os.remove(segment_path)

    SubtitleIO.write_srt(subtitle, output_path, output_format="source-only")
    print(f"字幕已生成：{output_path}")
    return str(output_path)


def _split_audio(audio: AudioSegment, audio_path: Path, max_length: int):
    base = audio_path.with_suffix("")
    extension = audio_path.suffix
    segments: list[Path] = []
    offsets: list[float] = []
    chunk_ms = max_length * 1000

    for start_ms in range(0, len(audio), chunk_ms):
        end_ms = min(start_ms + chunk_ms, len(audio))
        offset = start_ms / 1000.0
        segment_path = Path(f"{base}_segment_{offset:.0f}{extension}")
        chunk = cast(Any, audio)[start_ms:end_ms]
        chunk.export(segment_path, format="wav")
        segments.append(segment_path)
        offsets.append(offset)

    return segments, offsets
