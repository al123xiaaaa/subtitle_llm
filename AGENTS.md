# AGENTS.md

This file provides guidance to AI agents when working with code in this repository.

## Project Overview

A CLI tool that translates `.srt` subtitle files (or word-level `.json` transcripts) into bilingual subtitles using LLMs. Supports OpenAI, Gemini, and custom OpenAI-compatible endpoints. The output `.srt` contains both original and translated text per entry. An Electron + Vue desktop frontend (`electron/`) wraps the same pipeline by driving the CLI as a child process. The codebase uses Chinese for comments, logs, and user-facing prompts.

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

# Desktop frontend (Electron + Vue; requires Node >= 22.18)
npm install
npm run desktop            # build + launch the desktop app
npm run desktop:dev        # dev mode
npm run lint:desktop       # oxlint + eslint (Vue renderer)
npm run typecheck:desktop  # vue-tsc --noEmit
npm run test:desktop       # lint + build + typecheck + smoke + Playwright e2e
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
6. `TranslationTaskStore` (`task_store.py`) persists resumable runs in SQLite with input/config snapshots; `checkpoint.py` provides sidecar path and file fingerprint helpers.
7. `ReviewPolicy` (`review_policy.py`) decides the route for suspicious chunks; `chunk_acceptance.py` runs the (parallelized) acceptance pipeline: quality diagnosis → auto repair/re-translate → semantic layout repair → source correction gate → TUI review.

**Model segmentation** (`pipeline/model_segmentation.py` + `model_segmenter.py`): for ASR sources (config `pipeline.model_segmentation: auto|always|off`, default `auto`), segmentation and translation happen in a single model call — cue boundaries come from model-chosen end positions, validated for contiguous coverage against the source text. Only missing ranges are repaired (max 2 repair calls); generated/reviewed results are persisted and reused across resumes. See `docs/model-segmentation.md`.

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

**Progress & observability**:
- `progress_contract.py` / `progress_events.py` (package root) — the stdout progress-event contract shared by the CLI and the desktop frontend; `ProgressEmitter` emits JSON events parsed on the desktop side by `electron/lib/stdoutProtocol.ts`
- `pipeline/run_ledger.py` — `RunLedger` run history and task-state restore
- `pipeline/llm_trace.py` — `LlmTraceRecorder`, per-run LLM call traces with source coverage
- `pipeline/report.py` — `TranslationReport` run summary
- `pipeline/lifecycle/` — task lifecycle state machines, events, and projectors

**Media & ASR** (`src/subtitle_llm/media/`):
- `downloader.py` — fetches video/audio/subtitles via yt-dlp
- `asr_backend.py` — `AsrBackend` protocol, `AsrCue`, and `LlamacppAsrBackend` (FunASR llama.cpp / GGUF runtime, no PyTorch)
- `transcriber.py` — orchestrates ASR into a source-only SRT; language mapping and tag cleaning
- `muxer.py` — embeds the translated SRT as a soft subtitle track in an MKV
- ASR runs two prebuilt binaries (`llama-funasr-vad` for timestamps, `llama-funasr-sensevoice` for text); see `docs/adr/0002-asr-backend-migrate-to-llamacpp.md`

**Desktop frontend** (`electron/` — Electron + Vue 3 + TypeScript, built with Vite):
- Drives the Python CLI as a child process (`lib/cliCommands.ts` spawns `main.py translate --task-id ... --resume`); `lib/stdoutProtocol.ts` is the single parser of the CLI's progress-event protocol
- `src/subtitle_llm/config/desktop-contract.json` is the single source of shared defaults (model params, ASR defaults, ffmpeg path) for both sides; Python-side consistency is locked by `tests/test_config_contract.py`
- `main.ts` / `preload.ts` — Electron main and preload; `renderer/` — Vue UI
- `tests/` — smoke and Playwright e2e
- PRDs: `docs/prd-electron-user-friendly-model-configuration.md`, `docs/prd-desktop-work-progress-visibility.md`

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
- Desktop frontend (Node devDependencies in `package.json`): `electron`, `vue` + `vite`, `vue-tsc`/`typescript`, `oxlint` + `eslint` (incl. `eslint-plugin-vue`), `playwright`

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (`al123xiaaaa/subtitle_llm`) via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
