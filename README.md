# Subtitle LLM

CLI tool for translating `.srt` subtitle files, word-level transcript `.json` files, or video URLs into bilingual subtitles using LLMs.

## Usage

Install the package in editable mode:

```bash
pip install -e .
```

Set the API key expected by the default config:

```bash
export GEMINI_API_KEY="..."
```

Translate a local subtitle file:

```bash
subtitle-llm translate --input input.srt --target-language Chinese
```

Translate without installing the console script:

```bash
python3 main.py translate --input input.srt --target-language Chinese
```

By default, translated subtitles are written to `data/output/<title>.<target>.srt`.
Use `--output` only when you want to override that path.

Other commands:

```bash
subtitle-llm download "https://example.com/video" --output-dir data/input --source-language en
subtitle-llm transcribe data/input/audio.wav --output data/input/audio.srt --language English
```

ASR models are resolved through the Hugging Face cache. After the first successful download,
later runs prefer the local cached snapshot; set `asr.cache_dir` in your config if you want
to keep these model files somewhere other than the default Hugging Face cache directory.

## Project structure

```
subtitle_llm/
├── pyproject.toml
├── main.py
├── src/
│   ├── subtitle_llm/
│   │   ├── cli/          # Typer commands
│   │   ├── config/       # bundled default config
│   │   ├── domain/       # subtitle/transcript models
│   │   ├── io/           # SRT and JSON readers/writers
│   │   ├── llm/          # provider clients and shared protocol
│   │   ├── media/        # download/transcribe adapters
│   │   ├── pipeline/     # translation orchestration services
│   │   └── review/       # auto/TUI review ports
├── tests/
├── data/
└── requirements.txt
```

## Configuration

The default config is `src/subtitle_llm/config/default.yaml`. It uses environment variable references instead of hardcoded secrets:

```yaml
summary_model:
  provider: "gemini"
  api_key_env: "GEMINI_API_KEY"
  model: "gemini-3.1-flash-lite-preview"
```

Most fallback defaults live in `src/subtitle_llm/settings.py`; `default.yaml` only declares required model settings and project-specific overrides.

Use a custom config with:

```bash
subtitle-llm translate --config ./my-config.yaml --input input.srt --output output.srt --target-language Chinese
```

## LLM Translation Method

1. Generate a summary context of the subtitle, including:
   - Video topics
   - A short summary of the subtitle content
   - A list of untranslatable terms (technical terms, proper nouns, etc.)

2. Divide the subtitle into chunks for processing.

3. For each chunk:
   - Perform a rough translation into the target language.
   - Refine the translation using the original subtitle, rough translation, and context.
   - Fix suspicious or missing translations automatically, or send them to TUI review with `--review`.

4. Combine the refined translations with the original subtitles to form the final bilingual .srt file.

5. Done.

Key features:
- Maintains context throughout the translation process
- Handles subtitle chunks in parallel for improved performance
- Preserves subtitle timing and formatting
- Ensures accurate translation of technical terms and proper nouns
- Fixes missing translations automatically

Note: The translation process uses multiple LLM calls to ensure high-quality results while maintaining the original subtitle structure and timing.
