# AGENTS.md

This file provides guidance to AI agents when working with code in this repository.

## Project Overview

A CLI tool that translates `.srt` subtitle files (or word-level `.json` transcripts) into bilingual subtitles using LLMs. Supports OpenAI, Gemini, and custom OpenAI-compatible endpoints. The output `.srt` contains both original and translated text per entry. The codebase uses Chinese for comments, logs, and user-facing prompts.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run translation
python3 main.py -i <input.srt|input.json> -o <output.srt> -to <target_language>

# Run with custom TUI review (default behavior)
python3 main.py -i ./data/input/input_1.json -o ./data/output/output_1.srt -to "Chinese"

# Run without TUI (auto-fix missing translations)
python3 main.py -i ./data/input/input_1.json -o ./data/output/output_1.srt -to "Chinese" -ch False

# Run tests
python -m unittest discover tests
```

No build system, linter, or formatter is configured.

## Architecture

**Entry point:** `main.py` — parses CLI args, calls `translate_subtitles()`.

**Translation pipeline** (`src/services/translator.py`):
1. Parse input via `FileHandler` (.srt) or `JSONSubtitleHandler` (.json)
2. Generate context summary via LLM (`generate_summary_and_terms()`)
3. Split subtitles into chunks (~34 entries each), translate in parallel (`ThreadPoolExecutor`, 10 threads)
4. Per chunk: rough translate → refine → detect missing translations → fix or open TUI for manual review
5. Write bilingual output `.srt`

**LLM client architecture** (Factory + Strategy):
- `src/services/llm_clients/llm_client.py` — abstract base class
- `openai_client.py` — OpenAI SDK (works with any compatible endpoint, e.g. DeepSeek)
- `gemini_client.py` — Google Gemini SDK with built-in retry
- `custom_llm_client.py` — raw HTTP via `requests`
- `src/services/factories/llm_client_factory.py` — creates the right client from config, wraps with `RateLimiter` if configured

**TUI review system** (`src/services/tui/`):
- `tui_manager.py` — opens a new terminal window for the TUI app
- `custom_handling.py` — Textual-based TUI for reviewing/editing translations (merge, re-translate, skip)
- Data passed between main process and TUI via temp JSON files

**Prompt templates:** `src/utils/prompts.py` — all LLM prompts (summary, translate, refine, fix, re-translate).

**Configuration:** `src/config/config.yaml` — chunk size, thread count, rate limits, LLM model configs. `config.yaml` contains hardcoded API keys.

**Key models:** `SubtitleEntry` (single subtitle with index/timing/original/translated), `Subtitle` (list container), `Word`/`Segment`/`Transcript` (JSON transcript parsing).

## Key Dependencies

- `openai` — OpenAI SDK (also used for DeepSeek)
- `google-generativeai` — Gemini SDK
- `textual` — TUI framework for interactive review
- `chardet` — encoding detection for SRT files
- `tiktoken` — token counting
- `PyYAML` — config parsing
