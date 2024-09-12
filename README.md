# .srt subtitle translator

This is a tool to translate .srt subtitle files using Large Language Models (LLMs). It generates a bilingual subtitle file containing both the original text and the translated text.

## Usage

```bash
python3 main.py -i input.srt -o output.srt -to "chinese"
```

## Project structure

```
subtitle_llm/
│
├── src/
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── subtitle.py
│   │   └── subtitle_entry.py
│   └── services/
│       ├── __init__.py
│       ├── file_handler.py
│       └── translator.py
│
├── tests/
│   ├── __init__.py
│   ├── test_subtitle.py
│   ├── test_subtitle_entry.py
│   ├── test_file_handler.py
│   └── test_translator.py
│
├── data/
│   ├── input/
│   └── output/
│
├── config/
│   └── config.yaml
│
├── main.py
├── requirements.txt
└── README.md
```

## LLM translation method

1. Generate a summary context of the subtitle, including:
   - Video topics
   - A short summary of the subtitle content
   - A list of untranslatable terms (technical terms, proper nouns, etc.)

2. Divide the subtitle into chunks for processing.

3. For each chunk:
   a. Perform a rough translation into the target language.
   b. Refine the translation using the original subtitle, rough translation, and context.
   c. Fix any missing translations if necessary.

4. Combine the refined translations with the original subtitles to form the final bilingual .srt file.

5. Done.

Key features:
- Maintains context throughout the translation process
- Handles subtitle chunks in parallel for improved performance
- Preserves subtitle timing and formatting
- Ensures accurate translation of technical terms and proper nouns
- Fixes missing translations automatically

Note: The translation process uses multiple LLM calls to ensure high-quality results while maintaining the original subtitle structure and timing.
