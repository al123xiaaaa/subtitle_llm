import os
from urllib.parse import urlparse

from src.services.file_handler import FileHandler
import argparse


def parse_bool(value):
    if isinstance(value, bool):
        return value

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("请使用 true/false、yes/no、1/0")


def is_url(value):
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def download_and_prepare(url: str, source_language: str):
    """Download video and subtitles from URL. Returns the .srt input file path."""
    from src.services.video_downloader import download
    from src.services.audio_transcriber import transcribe
    from src.utils.utility_functions import load_yaml_config

    output_dir = os.path.join(os.path.dirname(__file__), "data", "input")
    result = download(url, output_dir, source_language)

    if len(result) == 3:
        video_path, subtitle_path, audio_path = result
    else:
        video_path, subtitle_path = result
        audio_path = None

    if subtitle_path:
        print(f"已下载字幕：{subtitle_path}")
        return subtitle_path

    # No subtitles — use Qwen3-ASR to generate
    if not audio_path:
        print("错误：未找到字幕且无法提取音频")
        return None

    print("未找到字幕，使用 Qwen3-ASR 自动生成...")
    config = load_yaml_config().get("asr", {})
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    srt_path = os.path.join(output_dir, f"{base_name}.srt")
    return transcribe(audio_path, source_language, srt_path, config)


def main(
    input_file: str,
    output_file: str,
    target_language: str,
    custom_handling: bool,
    source_language: str = "en",
    output_format: str = "source-first",
    resume: bool = False,
):
    from src.services.translator import translate_subtitles

    # If input is a URL, download video + subtitles first
    if is_url(input_file):
        srt_path = download_and_prepare(input_file, source_language)
        if not srt_path:
            return
        input_file = srt_path

    # Translate the subtitles
    translated_subtitle, report = translate_subtitles(
        input_file=input_file,
        output_file=output_file,
        target_language=target_language,
        custom_handling=custom_handling,
        output_format=output_format,
        resume=resume,
        return_report=True,
    )

    # Write the translated subtitles to the output file
    FileHandler.write_srt(translated_subtitle, output_file, output_format=output_format)

    print("\n===== 翻译完成 =====")
    print(f"输出文件：{output_file}")
    print(f"上下文文件：{report['context_file']}")
    print(f"断点文件：{report['checkpoint_file']}")
    print(f"输出格式：{output_format}")
    print(f"字幕条数：{report['total_entries']}，已处理：{report['processed_entries']}，短句保留：{report['short_entries']}")
    successful_chunks = report["completed_chunks"] - len(report["failed_chunks"])
    print(f"Chunk：成功 {successful_chunks} / {report['total_chunks']}，失败 {len(report['failed_chunks'])}")
    print(f"疑似跨 Chunk 断句边界：{report.get('boundary_risk_count', 0)}")
    print(
        "Token："
        f"{report['token_usage']['total_tokens']} "
        f"(prompt={report['token_usage']['prompt_tokens']}, "
        f"completion={report['token_usage']['completion_tokens']})"
    )
    if report["failed_chunks"]:
        print("失败 chunk：")
        for failed in report["failed_chunks"]:
            print(f"- #{failed['chunk_index'] + 1}: {failed['error']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="加载、翻译并保存字幕文件")
    parser.add_argument(
        "-i", "--input", required=True,
        help="输入文件路径（.srt 或 .json）或视频 URL"
    )
    parser.add_argument("-o", "--output", required=True, help="输出 .srt 文件路径")
    parser.add_argument("-to", "--target", required=True, help="目标翻译语言")
    parser.add_argument(
        "-ch",
        "--custom",
        required=False,
        nargs="?",
        const=True,
        default=None,
        type=parse_bool,
        help="是否启用人工 TUI 审核（兼容旧参数；推荐使用 --review 或 --no-review）",
    )
    review_group = parser.add_mutually_exclusive_group()
    review_group.add_argument(
        "--review",
        dest="review",
        action="store_true",
        default=None,
        help="启用人工 TUI 审核缺失或异常翻译（默认）",
    )
    review_group.add_argument(
        "--no-review",
        "--auto-fix",
        dest="review",
        action="store_false",
        help="不打开 TUI，自动修复缺失或异常翻译",
    )
    parser.add_argument(
        "-sl", "--source-language", required=False, default="en",
        help="源语言（用于下载字幕和 ASR，默认 en）"
    )
    parser.add_argument(
        "--format",
        choices=["bilingual", "source-first", "target-first", "target-only", "source-only"],
        default="source-first",
        help="输出字幕文本格式，bilingual 等同 source-first",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="从输出旁边的断点文件继续未完成的翻译",
    )

    args = parser.parse_args()
    custom_handling = (
        args.review
        if args.review is not None
        else args.custom
        if args.custom is not None
        else True
    )
    main(
        args.input,
        args.output,
        args.target,
        custom_handling,
        args.source_language,
        args.format,
        args.resume,
    )
