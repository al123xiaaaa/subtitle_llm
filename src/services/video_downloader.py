import os
import re
from yt_dlp import YoutubeDL


def _sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def download(url: str, output_dir: str, source_language: str = "en"):
    """
    Download video and subtitles from a URL.

    Returns:
        (video_path, subtitle_path or None)
        subtitle_path is None when no subtitles are available.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Check available subtitles
    with YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(url, download=False)

    title = _sanitize_filename(info.get("title", "video"))
    manual_subs = info.get("subtitles", {})
    auto_subs = info.get("automatic_captions", {})

    lang_code = _get_lang_code(source_language)
    has_manual = lang_code in manual_subs
    has_auto = lang_code in auto_subs

    outtmpl = os.path.join(output_dir, f"{title}.%(ext)s")

    if has_manual or has_auto:
        print(f"找到字幕（{'人工' if has_manual else '自动生成'}），正在下载...")
        ydl_opts = {
            "format": "bestvideo+bestaudio/best",
            "outtmpl": outtmpl,
            "writesubtitles": True,
            "writeautomaticsub": not has_manual,
            "subtitleslangs": [lang_code],
            "subtitlesformat": "srt",
            "postprocessors": [
                {"key": "FFmpegSubtitlesConvertor", "format": "srt"},
            ],
        }
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        video_path = _find_file(output_dir, title, ".mp4", ".mkv", ".webm")
        subtitle_path = _find_file(output_dir, title, f".{lang_code}.srt", ".srt")
        return video_path, subtitle_path

    # No subtitles — download video + extract audio for ASR
    print("未找到字幕，正在下载视频并提取音频...")
    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": outtmpl,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            },
        ],
    }
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    video_path = _find_file(output_dir, title, ".mp4", ".mkv", ".webm")
    # Audio was extracted as wav
    audio_path = _find_file(output_dir, title, ".wav")
    return video_path, None, audio_path


def _get_lang_code(language: str) -> str:
    mapping = {
        "chinese": "zh", "english": "en", "japanese": "ja",
        "korean": "ko", "french": "fr", "german": "de",
        "spanish": "es", "italian": "it", "portuguese": "pt",
        "russian": "ru", "cantonese": "yue",
    }
    return mapping.get(language.lower(), language.lower()[:2])


def _find_file(directory: str, base_name: str, *extensions: str):
    for ext in extensions:
        path = os.path.join(directory, base_name + ext)
        if os.path.exists(path):
            return path
    # Fallback: find any file matching the base name with one of the extensions
    if os.path.isdir(directory):
        for f in os.listdir(directory):
            name, ext = os.path.splitext(f)
            if name.startswith(base_name) and ext in extensions:
                return os.path.join(directory, f)
    return None
