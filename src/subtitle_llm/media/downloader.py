from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, cast

from yt_dlp import YoutubeDL

from subtitle_llm.progress_events import ProgressEmitter

logger = logging.getLogger(__name__)

# 与桌面端 electron/lib/ffmpegStatus.ts 的常见安装位置保持一致
_COMMON_FFMPEG_PATHS = ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg")


def _resolve_ffmpeg_location() -> str | None:
    """解析 yt-dlp 可用的 ffmpeg 路径。

    桌面端从 Finder 启动时 PATH 不含 Homebrew，yt-dlp 默认按 PATH 查找会失败；
    依次尝试环境变量、PATH、常见安装位置。找不到时返回 None，调用方降级处理。
    """
    for candidate in (os.getenv("SUBTITLE_LLM_FFMPEG"), os.getenv("FFMPEG_BINARY")):
        if candidate and Path(candidate).exists():
            return candidate
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in _COMMON_FFMPEG_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def download(
    url: str,
    output_dir: str | Path,
    source_language: str = "en",
    progress: ProgressEmitter | None = None,
    force_asr: bool = False,
):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    emit_progress(
        progress,
        "metadata",
        "读取视频元信息",
        "正在读取视频元信息",
    )
    logger.info(
        "读取视频元信息: url=%s output_dir=%s source_language=%s force_asr=%s",
        url,
        output_path,
        source_language,
        force_asr,
    )
    with YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(url, download=False)

    title = _sanitize_filename(str(info.get("title") or "video"))
    manual_subs = info.get("subtitles", {})
    auto_subs = info.get("automatic_captions", {})
    lang_code = _get_lang_code(source_language)
    has_manual = lang_code in manual_subs
    has_auto = lang_code in auto_subs
    outtmpl = str(output_path / f"{title}.%(ext)s")
    video_path = _find_file(output_path, title, ".mp4", ".mkv", ".webm")
    subtitle_path = _find_file(output_path, title, f".{lang_code}.srt", ".srt")
    audio_path = _find_file(output_path, title, ".wav")

    if subtitle_path and not force_asr:
        emit_progress(
            progress,
            "reuse_subtitle",
            "复用字幕",
            f"复用已下载字幕：{subtitle_path}",
            status="done",
        )
        logger.info("复用已下载字幕: title=%s video=%s subtitle=%s", title, video_path, subtitle_path)
        return video_path, subtitle_path
    if audio_path:
        emit_progress(
            progress,
            "reuse_audio",
            "复用音频",
            f"复用已下载音频：{audio_path}",
            status="done",
        )
        logger.info("复用已下载音频: title=%s video=%s audio=%s", title, video_path, audio_path)
        return video_path, None, audio_path

    if (has_manual or has_auto) and not force_asr:
        emit_progress(
            progress,
            "download_subtitle",
            "下载字幕",
            f"正在下载视频和{'人工' if has_manual else '自动'}字幕",
        )
        logger.info("开始下载视频和字幕: title=%s lang=%s manual_subtitle=%s", title, lang_code, has_manual)
        ffmpeg_location = _resolve_ffmpeg_location()
        # 无 ffmpeg 时无法合并分离的音视频流，降级为单文件渐进流格式
        video_format = "bestvideo+bestaudio/best" if ffmpeg_location else "best[ext=mp4]/best"
        ydl_opts: dict[str, Any] = {
            "format": video_format,
            "outtmpl": outtmpl,
            "writesubtitles": True,
            "writeautomaticsub": not has_manual,
            "subtitleslangs": [lang_code],
            "subtitlesformat": "srt",
            "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}],
        }
        if ffmpeg_location:
            ydl_opts["ffmpeg_location"] = ffmpeg_location
        else:
            logger.warning("未找到 ffmpeg，视频降级为单文件格式下载：%s", video_format)
        with YoutubeDL(cast(Any, ydl_opts)) as ydl:
            ydl.download([url])
        result = (
            _find_file(output_path, title, ".mp4", ".mkv", ".webm"),
            _find_file(output_path, title, f".{lang_code}.srt", ".srt"),
        )
        emit_progress(
            progress,
            "download_subtitle",
            "下载字幕",
            "视频和字幕下载完成",
            status="done",
        )
        logger.info("视频和字幕下载完成: title=%s result=%s", title, result)
        return result

    emit_progress(
        progress,
        "extract_audio",
        "提取音频",
        "已选择 ASR，正在下载视频并提取音频" if force_asr else "未找到字幕，正在下载视频并提取音频",
    )
    logger.info("开始下载视频并提取音频: title=%s force_asr=%s", title, force_asr)
    ffmpeg_location = _resolve_ffmpeg_location()
    ydl_opts = {
        "format": "bestvideo+bestaudio/best" if ffmpeg_location else "best[ext=mp4]/best",
        "outtmpl": outtmpl,
        "keepvideo": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
    }
    if ffmpeg_location:
        ydl_opts["ffmpeg_location"] = ffmpeg_location
    else:
        logger.warning("未找到 ffmpeg，音频提取可能失败；视频降级为单文件格式下载")
    with YoutubeDL(cast(Any, ydl_opts)) as ydl:
        ydl.download([url])

    result = (
        _find_file(output_path, title, ".mp4", ".mkv", ".webm"),
        None,
        _find_file(output_path, title, ".wav"),
    )
    emit_progress(
        progress,
        "extract_audio",
        "提取音频",
        "视频和音频下载完成",
        status="done",
    )
    logger.info("视频和音频下载完成: title=%s result=%s", title, result)
    return result


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
        stage="prepare_input" if progress.command == "translate" else "download",
        detail=detail,
        status=status,
        label=label,
        message=message,
    )


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
