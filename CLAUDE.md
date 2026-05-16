# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A CLI tool that translates `.srt` subtitle files (or word-level `.json` transcripts) into bilingual subtitles using LLMs. Supports OpenAI, Gemini, and custom OpenAI-compatible endpoints. The output `.srt` contains both original and translated text per entry. The codebase uses Chinese for comments, logs, and user-facing prompts.

## Commands

```bash
# Install package in editable mode
pip install -e .

# Set API keys used by config
export GEMINI_API_KEY="..."

# Run translation
subtitle-llm translate --input <input.srt|input.json> --target-language <target_language>

# Run with TUI review
subtitle-llm translate --input ./data/input/input_1.json --target-language Chinese --review

# Run without installing console script
python3 main.py translate --input ./data/input/input_1.json --target-language Chinese

# Run tests
python -m unittest discover tests

# Static checks
ruff check .
pyright
```

## Architecture

**Package root:** `src/subtitle_llm/` — the only application package. The old `src/services`, `src/models`, and `src/utils` modules have been removed.

**Entry point:** `main.py` — thin compatibility launcher for the Typer app. Installed CLI entry point is `subtitle-llm`.

**Translation pipeline** (`src/subtitle_llm/pipeline/`):
1. `TranslationService` resolves local files or video URLs, reads subtitles, and orchestrates the flow.
2. `ContextService` generates summary and terminology context.
3. `ChunkPlanner` splits subtitles and builds readonly boundary context.
4. `ChunkTranslator` performs rough translation, refinement, repair, and re-translation.
5. `QualityGate` performs deterministic quality diagnosis, groups repeated/cascading failures, and generates compact reports for re-translation.
6. `CheckpointStore` persists resumable progress with input/config metadata.
7. `ReviewPort` routes suspicious chunks to auto repair or TUI review.

**LLM client architecture** (`src/subtitle_llm/llm/`):
- `types.py` — `ChatClient`, `CompletionResult`, and normalized usage models
- `clients.py` — OpenAI, Gemini, and custom HTTP provider adapters
- `factory.py` — creates provider clients from typed config
- `rate_limiter.py` and `token_counter.py` — shared client utilities

**TUI review system** (`src/subtitle_llm/review/`):
- `tui_manager.py` — opens a terminal worker
- `tui_worker.py` — handles file-based IPC queue
- `custom_handling.py` — Textual app for review, merge, re-translate, and skip
- Data passed between main process and TUI via temp JSON files

**Prompt templates:** `src/subtitle_llm/pipeline/prompts.py`.

**Configuration:** `src/subtitle_llm/config/default.yaml` plus optional `--config`. Config is validated by Pydantic in `settings.py`. API keys are referenced by environment variable names such as `GEMINI_API_KEY`; do not hardcode secrets.

**Key models:** `SubtitleEntry`, `Subtitle`, `Word`, `Segment`, and `Transcript` live in `src/subtitle_llm/domain/`.

## Key Dependencies

- `typer` — CLI framework
- `pydantic` — config validation
- `openai` — OpenAI SDK (also used for DeepSeek)
- `google-generativeai` — Gemini SDK
- `textual` — TUI framework for interactive review
- `chardet` — encoding detection for SRT files
- `tiktoken` — token counting
- `PyYAML` — config parsing
