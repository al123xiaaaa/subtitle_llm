from src.services.file_handler import FileHandler
from src.services.translator import translate_subtitles
import argparse

def main(input_file: str, output_file: str, target_language: str):
    # Read the input file
    # subtitle = FileHandler.read_srt(input_file)

    # Print the original subtitle contents
    # print("Original subtitles:")
    # print(subtitle)

    # Translate the subtitles
    translated_subtitle = translate_subtitles(input_file, output_file, target_language)

    # Print the translated subtitle contents
    # print("\nTranslated subtitles:")
    # print(translated_subtitle)

    # Write the translated subtitles to the output file
    FileHandler.write_srt(translated_subtitle, output_file)
    print(f"\nTranslated subtitles written to {output_file}")

if __name__ == "__main__":
    # Set up argument parsing
    parser = argparse.ArgumentParser(description="Load, translate, and save a .srt file")
    parser.add_argument('-i', '--input', required=True, help="Input .srt file path")
    parser.add_argument('-o', '--output', required=True, help="Output .srt file path for translated subtitles")
    parser.add_argument('-to', '--target', required=True, help="Target language for translation")
    
    # Parse arguments
    args = parser.parse_args()

    # Call main function with input, output files, and target language
    main(args.input, args.output, args.target)
