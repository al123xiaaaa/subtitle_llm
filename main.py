import os
import validators
from src.services.file_handler import FileHandler
from src.services.translator import translate_subtitles
import argparse


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


def main(input_file: str, output_file: str, target_language: str,
         custom_handling: bool, source_language: str = "en"):
    # If input is a URL, download video + subtitles first
    if validators.url(input_file):
        srt_path = download_and_prepare(input_file, source_language)
        if not srt_path:
            return
        input_file = srt_path

    # Translate the subtitles
    translated_subtitle = translate_subtitles(
        input_file=input_file,
        output_file=output_file,
        target_language=target_language,
        custom_handling=custom_handling,
    )

    # Write the translated subtitles to the output file
    FileHandler.write_srt(translated_subtitle, output_file)
    print(f"\n翻译完成并保存为 .srt 文件：{output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="加载、翻译并保存字幕文件")
    parser.add_argument(
        "-i", "--input", required=True,
        help="输入文件路径（.srt 或 .json）或视频 URL"
    )
    parser.add_argument("-o", "--output", required=True, help="输出 .srt 文件路径")
    parser.add_argument("-to", "--target", required=True, help="目标翻译语言")
    parser.add_argument(
        "-ch", "--custom", required=False, help="自定义调整翻译问题",
        default=True, action="store_true"
    )
    parser.add_argument(
        "-sl", "--source-language", required=False, default="en",
        help="源语言（用于下载字幕和 ASR，默认 en）"
    )

    args = parser.parse_args()
    main(args.input, args.output, args.target, args.custom, args.source_language)
