from src.services.file_handler import FileHandler
from src.services.translator import translate_subtitles
import argparse


def main(input_file: str, output_file: str, target_language: str):
    # Translate the subtitles
    translated_subtitle = translate_subtitles(
        input_file=input_file,
        output_file=output_file,
        target_language=target_language,
    )

    # Write the translated subtitles to the output file
    FileHandler.write_srt(translated_subtitle, output_file)
    print(f"\n翻译完成并保存为 .srt 文件：{output_file}")


if __name__ == "__main__":
    # Set up argument parsing
    parser = argparse.ArgumentParser(description="加载、翻译并保存字幕文件")
    parser.add_argument(
        "-i", "--input", required=True, help="输入文件路径（.srt 或 .json）"
    )
    parser.add_argument("-o", "--output", required=True, help="输出 .srt 文件路径")
    parser.add_argument("-to", "--target", required=True, help="目标翻译语言")

    # Parse arguments
    args = parser.parse_args()

    # Call main function with input, output files, target language, and input format
    main(args.input, args.output, args.target)
