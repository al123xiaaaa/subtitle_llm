from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from pydub import AudioSegment

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.io import SubtitleIO, seconds_to_srt_time
from subtitle_llm.settings import ASRConfig


SENTENCE_ENDINGS: Final[tuple[str, ...]] = (".", "!", "?", "。", "！", "？")
CJK_ASR_LANGUAGES: Final[set[str]] = {"Chinese", "Japanese", "Cantonese"}
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


@dataclass(frozen=True)
class ASRTimeSpan:
    text: str
    start_time: float
    end_time: float


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
    model_path = _resolve_model_path(config.model, config.cache_dir, config.prefer_local_cache)
    forced_aligner_path = _resolve_model_path(config.forced_aligner, config.cache_dir, config.prefer_local_cache)
    if asr_language != language:
        display_language = "自动识别" if asr_language is None else asr_language
        print(f"ASR 语言：{display_language}（来自 {language}）")

    print(f"正在加载 Qwen3-ASR 模型 ({config.model})...")
    model = Qwen3ASRModel.from_pretrained(
        model_path,
        forced_aligner=forced_aligner_path,
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
            entries = _time_stamps_to_subtitle_entries(
                result.time_stamps,
                offset=offset,
                start_index=entry_index,
                language=result.language or asr_language,
                config=config,
                reference_text=result.text,
            )
            for entry in entries:
                subtitle.add_entry(entry)
            entry_index += len(entries)

        if segment_path != audio_path:
            os.remove(segment_path)

    SubtitleIO.write_srt(subtitle, output_path, output_format="source-only")
    print(f"字幕已生成：{output_path}")
    return str(output_path)


def _resolve_model_path(model: str, cache_dir: str | None, prefer_local_cache: bool) -> str:
    model_path = Path(model).expanduser()
    if model_path.exists():
        return str(model_path)

    expanded_cache_dir = str(Path(cache_dir).expanduser()) if cache_dir else None
    if prefer_local_cache:
        try:
            cached_path = _download_model_snapshot(model, expanded_cache_dir, local_files_only=True)
            print(f"使用本地模型缓存：{model}")
            return cached_path
        except Exception:
            pass

    print(f"本地缓存未命中，准备下载模型：{model}")
    return _download_model_snapshot(model, expanded_cache_dir, local_files_only=False)


def _download_model_snapshot(model: str, cache_dir: str | None, local_files_only: bool) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id=model, cache_dir=cache_dir, local_files_only=local_files_only)


def _time_stamps_to_subtitle_entries(
    time_stamps: Any,
    offset: float,
    start_index: int,
    language: str | None,
    config: ASRConfig,
    reference_text: str | None = None,
) -> list[SubtitleEntry]:
    spans = [_to_time_span(item, offset) for item in time_stamps]
    spans = [span for span in spans if span.text]
    if reference_text:
        spans = _apply_reference_punctuation(spans, reference_text, language)
    if not spans:
        return []

    entries: list[SubtitleEntry] = []
    buffer: list[ASRTimeSpan] = []

    def flush() -> None:
        if not buffer:
            return
        entries.append(
            SubtitleEntry(
                index=start_index + len(entries),
                start_time=seconds_to_srt_time(buffer[0].start_time),
                end_time=seconds_to_srt_time(max(buffer[-1].end_time, buffer[0].start_time)),
                original_text=_join_asr_text([span.text for span in buffer], language),
            )
        )
        buffer.clear()

    for span in spans:
        if buffer and _should_split_before(buffer, span, language, config):
            flush()

        buffer.append(span)
        if span.text.endswith(SENTENCE_ENDINGS):
            flush()

    flush()
    return entries


def _to_time_span(item: Any, offset: float) -> ASRTimeSpan:
    start_time = float(item.start_time) + offset
    end_time = max(float(item.end_time) + offset, start_time)
    return ASRTimeSpan(text=str(item.text).strip(), start_time=start_time, end_time=end_time)


def _should_split_before(
    buffer: list[ASRTimeSpan],
    next_span: ASRTimeSpan,
    language: str | None,
    config: ASRConfig,
) -> bool:
    gap_seconds = next_span.start_time - buffer[-1].end_time
    prospective_text = _join_asr_text([span.text for span in [*buffer, next_span]], language)
    prospective_duration = max(next_span.end_time, buffer[-1].end_time) - buffer[0].start_time
    return (
        gap_seconds >= config.subtitle_gap_seconds
        or len(prospective_text) > config.subtitle_max_chars
        or prospective_duration > config.subtitle_max_duration
    )


def _join_asr_text(parts: list[str], language: str | None) -> str:
    if language in CJK_ASR_LANGUAGES:
        text = "".join(parts)
    else:
        text = " ".join(parts)

    text = re.sub(r"\s+([,.;:!?%，。；：！？])", r"\1", text)
    text = re.sub(r"([(（])\s+", r"\1", text)
    text = re.sub(r"\s+([)）])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _apply_reference_punctuation(
    spans: list[ASRTimeSpan],
    reference_text: str,
    language: str | None,
) -> list[ASRTimeSpan]:
    if language in CJK_ASR_LANGUAGES:
        return spans

    reference_words = reference_text.split()
    if not reference_words:
        return spans

    updated_spans: list[ASRTimeSpan] = []
    reference_index = 0
    for span in spans:
        match_index = _find_reference_word(reference_words, reference_index, span.text)
        if match_index is None:
            updated_spans.append(span)
            continue

        updated_spans.append(
            ASRTimeSpan(
                text=reference_words[match_index],
                start_time=span.start_time,
                end_time=span.end_time,
            )
        )
        reference_index = match_index + 1

    return updated_spans


def _find_reference_word(reference_words: list[str], start_index: int, text: str) -> int | None:
    normalized_text = _normalize_reference_word(text)
    if not normalized_text:
        return None

    for index in range(start_index, min(start_index + 5, len(reference_words))):
        if _normalize_reference_word(reference_words[index]) == normalized_text:
            return index
    return None


def _normalize_reference_word(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", text).lower()


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
