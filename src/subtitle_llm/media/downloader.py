from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, cast

from yt_dlp import YoutubeDL


def download(url: str, output_dir: str | Path, source_language: str = "en"):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    with YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(url, download=False)

    title = _sanitize_filename(str(info.get("title") or "video"))
    manual_subs = info.get("subtitles", {})
    auto_subs = info.get("automatic_captions", {})
    lang_code = _get_lang_code(source_language)
    has_manual = lang_code in manual_subs
    has_auto = lang_code in auto_subs
    outtmpl = str(output_path / f"{title}.%(ext)s")

    if has_manual or has_auto:
        ydl_opts = {
            "format": "bestvideo+bestaudio/best",
            "outtmpl": outtmpl,
            "writesubtitles": True,
            "writeautomaticsub": not has_manual,
            "subtitleslangs": [lang_code],
            "subtitlesformat": "srt",
            "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}],
        }
        with YoutubeDL(cast(Any, ydl_opts)) as ydl:
            ydl.download([url])
        video_path = _find_file(output_path, title, ".mp4", ".mkv", ".webm")
        subtitle_path = _find_file(output_path, title, f".{lang_code}.srt", ".srt")
        return video_path, subtitle_path

    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": outtmpl,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
    }
    with YoutubeDL(cast(Any, ydl_opts)) as ydl:
        ydl.download([url])

    video_path = _find_file(output_path, title, ".mp4", ".mkv", ".webm")
    audio_path = _find_file(output_path, title, ".wav")
    return video_path, None, audio_path


def _sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def _get_lang_code(language: str) -> str:
    mapping = {
        "chinese": "zh",
        "english": "en",
        "japanese": "ja",
        "korean": "ko",
        "french": "fr",
        "german": "de",
        "spanish": "es",
        "italian": "it",
        "portuguese": "pt",
        "russian": "ru",
        "cantonese": "yue",
    }
    return mapping.get(language.lower(), language.lower()[:2])


def _find_file(directory: Path, base_name: str, *extensions: str):
    for extension in extensions:
        path = directory / f"{base_name}{extension}"
        if path.exists():
            return str(path)
    if directory.is_dir():
        for file_name in os.listdir(directory):
            name, extension = os.path.splitext(file_name)
            if name.startswith(base_name) and extension in extensions:
                return str(directory / file_name)
    return None
