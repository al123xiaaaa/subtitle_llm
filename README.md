# Subtitle LLM

CLI tool for translating `.srt` subtitle files, word-level transcript `.json` files, or video URLs into bilingual subtitles using LLMs.

## Usage

Install the package in editable mode:

```bash
pip install -e .
```

Start the desktop app:

```bash
npm install
npm run desktop
```

The desktop script builds the TypeScript Electron main/preload code and the Vue 3 renderer before launching.
For a build-only check, run:

```bash
npm run build:desktop
npm run typecheck:desktop
```

Run desktop smoke and Electron E2E tests:

```bash
npm run test:desktop
```

The Electron app keeps the Python pipeline as the source of truth. It provides forms for translating subtitles,
downloading subtitles, transcribing audio, and generating MKV files with soft subtitle tracks, then streams the CLI
output into the desktop log panel.
If you want the app to use a specific Python interpreter, set `SUBTITLE_LLM_PYTHON` before starting Electron.
On first launch, configure a translation service API Key in the setup prompt or the Settings page. The app
prefers locally saved keys, then falls back to environment variables such as `DEEPSEEK_API_KEY`.
In the translation view, choose a service and model; DeepSeek defaults to `deepseek-v4-flash` and also offers
`deepseek-v4-pro`. Advanced users can still use a YAML config, and generated runtime configs are passed to
the CLI with `--config`.
To generate a video after translation, install FFmpeg and enable "翻译完成后生成带字幕 MKV". URL inputs reuse the
downloaded video automatically; local subtitle inputs can be paired with a manually selected video.

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
subtitle-llm mux data/input/video.mp4 data/output/video.zh.srt --target-language Chinese
```

Generate an MKV automatically after translation:

```bash
subtitle-llm translate --input "https://example.com/video" --target-language Chinese --embed-video
subtitle-llm translate --input input.srt --target-language Chinese --video video.mp4 --embed-video
```

Video muxing creates a soft subtitle track in an `.mkv` file. Video and audio streams are copied without
transcoding, the translated subtitle track is marked as default, and existing output names are not overwritten.
By default, the MKV is written next to the translated SRT as `<video-name>.<target-code>.mkv`.

ASR models are resolved through the Hugging Face cache. After the first successful download,
later runs prefer the local cached snapshot; set `asr.cache_dir` in your config if you want
to keep these model files somewhere other than the default Hugging Face cache directory.

## Project structure

```
subtitle_llm/
├── pyproject.toml
├── main.py
├── electron/
│   ├── main.ts        # Electron main process
│   ├── preload.ts     # contextBridge IPC bridge
│   ├── lib/           # typed desktop runtime modules
│   └── renderer/      # Vue 3 + Vite renderer
├── scripts/           # desktop build scripts
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
   - Run deterministic quality diagnosis on the refined output.
   - Automatically re-translate suspicious chunks with a compact quality report, or send them to TUI review with `--review`.

4. Combine the refined translations with the original subtitles to form the final bilingual .srt file.

5. Done.

Key features:
- Maintains context throughout the translation process
- Handles subtitle chunks in parallel for improved performance
- Preserves subtitle timing and formatting
- Ensures accurate translation of technical terms and proper nouns
- Detects observable translation failures such as missing lines, placeholders, punctuation-only output, abnormal length, duplicated translations, source text copied into the output, language mismatch, lost numbers, and lost URL/command/code-like tokens
- Aggregates repeated or cascading failures into a short chunk-level diagnosis before re-translation, so the repair prompt receives the useful failure pattern without listing every bad line

Note: The translation process uses multiple LLM calls to ensure high-quality results while maintaining the original subtitle structure and timing.
