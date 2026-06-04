from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MuxError(RuntimeError):
    pass


class FfmpegNotFound(MuxError):
    pass


@dataclass(frozen=True)
class MuxResult:
    output_file: str
    command: list[str]


FILE_LANGUAGE_CODES = {
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

SUBTITLE_LANGUAGE_CODES = {
    "chinese": "zho",
    "english": "eng",
    "japanese": "jpn",
    "korean": "kor",
    "french": "fra",
    "german": "deu",
    "spanish": "spa",
    "italian": "ita",
    "portuguese": "por",
    "russian": "rus",
    "cantonese": "yue",
}


def is_ffmpeg_available(ffmpeg: str = "ffmpeg") -> bool:
    try:
        _resolve_ffmpeg(ffmpeg)
    except FfmpegNotFound:
        return False
    return True


def mux_subtitle_track(
    video_file: str | Path,
    subtitle_file: str | Path,
    output_file: str | Path | None = None,
    target_language: str = "Chinese",
    track_title: str | None = None,
    ffmpeg: str = "ffmpeg",
) -> MuxResult:
    video_path = _existing_file(video_file, "视频文件")
    subtitle_path = _existing_file(subtitle_file, "字幕文件")
    resolved_output = resolve_output_path(video_path, subtitle_path, target_language, output_file)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    command = build_mux_command(
        video_file=video_path,
        subtitle_file=subtitle_path,
        output_file=resolved_output,
        target_language=target_language,
        track_title=track_title,
        ffmpeg=_resolve_ffmpeg(ffmpeg),
    )
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        message = f"生成 MKV 失败：{detail}" if detail else "生成 MKV 失败"
        raise MuxError(message)

    return MuxResult(output_file=str(resolved_output), command=command)


def build_mux_command(
    video_file: str | Path,
    subtitle_file: str | Path,
    output_file: str | Path,
    target_language: str = "Chinese",
    track_title: str | None = None,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    title = track_title or f"{target_language} bilingual"
    language_code = subtitle_language_code(target_language)
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(video_file),
        "-i",
        str(subtitle_file),
        "-map",
        "0:v",
        "-map",
        "0:a?",
        "-map",
        "1:0",
        "-c",
        "copy",
        "-c:s",
        "srt",
        "-metadata:s:s:0",
        f"language={language_code}",
        "-metadata:s:s:0",
        f"title={title}",
        "-disposition:s:0",
        "default",
        str(output_file),
    ]


def resolve_output_path(
    video_file: str | Path,
    subtitle_file: str | Path,
    target_language: str,
    output_file: str | Path | None = None,
) -> Path:
    subtitle_path = Path(subtitle_file)
    if output_file:
        requested = Path(output_file)
        base_path = requested if requested.suffix else requested.with_suffix(".mkv")
    else:
        language_code = file_language_code(target_language)
        base_path = subtitle_path.parent / f"{Path(video_file).stem}.{language_code}.mkv"

    return _next_available_path(base_path)


def file_language_code(language: str) -> str:
    normalized = _normalize_language(language)
    if normalized in FILE_LANGUAGE_CODES:
        return FILE_LANGUAGE_CODES[normalized]
    compact = re.sub(r"[^a-z0-9]+", "", language.lower())
    return (compact[:8] or "sub")


def subtitle_language_code(language: str) -> str:
    normalized = _normalize_language(language)
    if normalized in SUBTITLE_LANGUAGE_CODES:
        return SUBTITLE_LANGUAGE_CODES[normalized]
    compact = re.sub(r"[^a-z0-9]+", "", language.lower())
    return (compact[:3] or "und")


def _resolve_ffmpeg(ffmpeg: str) -> str:
    executable = str(ffmpeg).strip() or "ffmpeg"
    explicit_path = Path(executable).expanduser()
    if explicit_path.exists():
        return str(explicit_path)
    discovered = shutil.which(executable)
    if discovered:
        return discovered
    raise FfmpegNotFound("未找到 FFmpeg。请先安装 FFmpeg，或用 --ffmpeg 指定可执行文件路径。")


def _existing_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.exists() or not path.is_file():
        raise MuxError(f"{label}不存在：{path}")
    return path


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}.{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise MuxError(f"无法生成不重名的输出文件：{path}")


def _normalize_language(language: str) -> str:
    return str(language or "").strip().lower()
