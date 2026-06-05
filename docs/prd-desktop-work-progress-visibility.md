## Problem Statement

Subtitle LLM 的 Electron 桌面端已经能启动 Python CLI、显示日志、输出字幕和 LLM 诊断目录，但普通用户在任务运行期间仍然缺少“程序正在做什么”的直观感知。

当前主反馈主要来自 stdout/stderr 日志和最终结果事件。日志适合排障，不适合等待中的普通用户。尤其是翻译任务包含下载、字幕解析、ASR、上下文生成、并发 chunk 翻译、质量检查、自动修复、TUI 复核、对齐漂移重译、断点保存、SRT 写出和 MKV 封装等多段流程。用户只看到日志时，很难判断程序是在正常等待模型、正在处理多个片段、等待自己去 TUI 复核，还是已经卡住。

这个问题不是“翻译进度”本身，而是“程序工作进度”：用户需要知道程序当前处于哪个阶段、最近做了什么、是否还在等待外部响应、哪些 chunk 正在并发处理、哪些 chunk 需要复核或带风险完成。

## Solution

为 Electron 桌面端增加面向普通用户的工作进度视图，并以 Python 结构化进度事件作为唯一事实来源。

用户启动任务后，主界面展示折叠式主阶段时间线、当前动作、等待时长、最近事件，以及 GitHub activity/contribution graph 风格的 chunk 并发状态图。日志面板保留，但降级为可展开的“详细日志”。

工作进度不使用全局百分比。界面重点表达“程序没死，它正在做哪件事”。对于长时间等待的模型请求、下载、ASR 或 FFmpeg 操作，界面显示等待时长和最近动作；超过 90 秒只做温和等待提示，不自动判定卡死。

Python CLI 发出稳定英文枚举的 `SUBTITLE_LLM_PROGRESS` 事件，Electron 实时消费并更新 UI。同一事件写入运行日志，方便事后复盘。LLM 诊断目录继续专注保存 prompt、response 和模型调用 JSON，不新增独立 progress 目录。

## User Stories

1. As a desktop subtitle translator, I want to see the current program stage, so that I know the app is still working.
2. As a desktop subtitle translator, I want the app to show what it is doing now, so that I do not need to infer progress from raw logs.
3. As a desktop subtitle translator, I want work progress to appear in the main run area, so that waiting feels predictable.
4. As a desktop subtitle translator, I want detailed logs to remain available but secondary, so that I can inspect problems without being forced to read logs during normal use.
5. As a desktop subtitle translator, I want the app to avoid misleading global percentages, so that I do not form false expectations about nonlinear LLM work.
6. As a desktop subtitle translator, I want to see a stage timeline, so that I understand the broad workflow from input preparation to output generation.
7. As a desktop subtitle translator, I want the current stage to expand with details, so that the UI stays readable while still covering the full workflow.
8. As a desktop subtitle translator, I want URL input to show metadata lookup, reuse, subtitle download, audio extraction, and ASR fallback stages, so that I understand why URL tasks may take longer.
9. As a desktop subtitle translator, I want local subtitle input to show file reading and subtitle normalization, so that local tasks do not look idle at startup.
10. As a desktop subtitle translator, I want context generation to be a visible stage, so that I know the model may be called before chunk translation begins.
11. As a desktop subtitle translator, I want optional context review to be visible, so that I know the program is waiting for user confirmation if that mode is enabled.
12. As a desktop subtitle translator, I want chunk planning to be visible, so that I understand why the app knows how many chunks will be processed.
13. As a desktop subtitle translator, I want chunk processing to show a compact activity grid, so that I can understand parallel work at a glance.
14. As a desktop subtitle translator, I want each activity square to represent one chunk, so that the UI maps directly to the translation pipeline.
15. As a desktop subtitle translator, I want chunk squares to be ordered by chunk number, so that I can locate earlier and later subtitle sections.
16. As a desktop subtitle translator, I want chunk colors to stay simple, so that I can scan the grid without decoding too many states.
17. As a desktop subtitle translator, I want chunk details available on hover or click, so that I can inspect chunk number, subtitle range, current detail, timing, token usage, and issue summary when needed.
18. As a desktop subtitle translator, I want running chunks to be shown as active, so that concurrent translation does not look like a single stuck chunk.
19. As a desktop subtitle translator, I want waiting chunks to remain visible, so that I understand the full planned workload.
20. As a desktop subtitle translator, I want completed chunks to stay visible after completion, so that I can see how much work has finished.
21. As a desktop subtitle translator, I want failed chunks to stand out, so that I know which part needs attention.
22. As a desktop subtitle translator, I want warning chunks to be distinct from clean success, so that I can review chunks that completed with residual risk.
23. As a desktop subtitle translator, I want chunk state to move backward when retranslation starts, so that the grid reflects the current truth, not only historical success.
24. As a desktop subtitle translator, I want short or no-op chunks to be marked as skipped, so that I do not mistake them for unprocessed work.
25. As a desktop subtitle translator, I want resumed chunks to appear done with a resume explanation, so that checkpoint recovery feels intentional.
26. As a desktop subtitle translator, I want automatic repair to be visible, so that I know the app is trying to fix suspicious model output.
27. As a desktop subtitle translator, I want TUI review waits to be visible in Electron, so that I know to handle the separate review window.
28. As a desktop subtitle translator, I want Electron to observe TUI status without controlling TUI, so that the existing review flow remains stable.
29. As a desktop subtitle translator, I want ordinary TUI retranslation to be visible, so that I know the selected rows are being regenerated.
30. As a desktop subtitle translator, I want alignment drift retranslation to be visible, so that I understand when the app is rebuilding a range from an indicated drift point.
31. As a desktop subtitle translator, I want the app to show waiting time for model responses, so that long LLM calls feel explainable rather than frozen.
32. As a desktop subtitle translator, I want the app to show a gentle long-wait hint after 90 seconds, so that I know the app is still waiting without being told it has failed.
33. As a desktop subtitle translator, I want true failures to come from process errors or caught pipeline errors, so that slow network work is not falsely marked as failed.
34. As a desktop subtitle translator, I want final results to keep the progress summary, so that I can review which chunks failed, warned, or required intervention.
35. As a desktop subtitle translator, I want chunk details to link to relevant LLM diagnostics when available, so that I can debug model output quickly.
36. As a desktop subtitle translator, I want model name and provider details hidden by default but available in details, so that the main UI remains user-friendly.
37. As a desktop subtitle translator, I want token and duration details available per chunk, so that I can reason about slow or expensive sections.
38. As a desktop user running download, I want linear download stages, so that non-translation tasks do not look idle.
39. As a desktop user running transcribe, I want language normalization, VAD, ASR model loading, transcription, and SRT writing stages, so that ASR work feels transparent.
40. As a desktop user running mux, I want input checking, output path resolution, FFmpeg execution, and completion stages, so that MKV generation has clear feedback.
41. As a CLI user, I want progress events to remain machine-readable, so that desktop and future clients can consume them reliably.
42. As a developer maintaining the Python pipeline, I want progress events to be emitted through a narrow interface, so that instrumentation does not scatter UI concerns through business logic.
43. As a developer maintaining Electron, I want progress state derived by a reducer, so that concurrent chunk updates are testable and App rendering remains simple.
44. As a developer maintaining tests, I want progress behavior tested through external events and visible state, so that refactors do not break the user experience.

## Implementation Decisions

- Prioritize Electron desktop as the main surface for work progress. CLI should emit the same structured progress events, but desktop receives the richest UI.
- Do not build a global percentage progress bar. The workflow is nonlinear and includes variable-duration model, network, TUI, ASR, and FFmpeg waits.
- Present a folded main timeline with the current stage expanded. The stage model must cover startup, input preparation, translation preparation, chunk processing, result generation, and command-specific linear flows.
- Cover the full translation task tree:
  - startup: load config, prepare model credentials, prepare run log, prepare LLM diagnostics;
  - input: local file read, URL metadata lookup, resource reuse, subtitle download, audio extraction, ASR fallback;
  - translation preparation: output path, checkpoint restore, subtitle normalization, context generation, optional context review, chunk planning and boundary context;
  - chunk processing: rough translation, refinement, response parsing, quality check, automatic repair, TUI review wait, ordinary retranslation, alignment drift retranslation, checkpoint save;
  - result generation: subtitle finalization, SRT write, optional MKV soft subtitle muxing, final result paths.
- Cover standalone `download`, `transcribe`, and `mux` commands with the same progress protocol. Their first UI can be linear rather than chunk-grid rich.
- Use Python-to-Electron structured events as the progress contract. Do not parse logs to drive UI state.
- Emit progress events to stdout with a stable prefix and JSON payload. Write the same event payload into the run log.
- Do not add a separate progress artifact directory. LLM diagnostics remain scoped to LLM prompt/response tracing.
- Use stable English enums in event payloads and Chinese product wording in UI rendering.
- Keep the event schema decision-rich but compact:

```json
{
  "command": "translate",
  "stage": "processing_chunks",
  "detail": "refine",
  "status": "running",
  "label": "润色",
  "message": "正在润色第 6/16 个片段",
  "elapsed_ms": 18400,
  "chunk": {
    "index": 6,
    "total": 16,
    "entry_start": 154,
    "entry_end": 182,
    "status": "running",
    "detail": "refine",
    "issue_summary": ""
  },
  "model": {
    "provider": "deepseek",
    "name": "deepseek-v4-flash"
  },
  "usage": {
    "prompt_tokens": 1200,
    "completion_tokens": 430,
    "total_tokens": 1630
  },
  "trace_id": "000014"
}
```

- The schema should tolerate missing optional fields. Non-LLM stages do not need model, usage, or trace ID.
- Chunk visual states should be limited to `waiting`, `running`, `review`, `repairing`, `done`, `warning`, `failed`, and `skipped`.
- Detailed chunk step names should remain available separately from visual state. Examples include `rough`, `refine`, `parse`, `quality`, `repair`, `tui_wait`, `tui_ordinary`, `drift`, and `checkpoint`.
- Chunk state is current-state first. A chunk that was done can move back to repairing if TUI retranslation starts.
- A chunk accepted after unresolved issues should end as `warning`, not clean `done`.
- Short/no-op chunks should be `skipped`. Checkpoint-resumed chunks should be `done` with a resume reason.
- The chunk activity UI should use a GitHub contribution graph style: one compact square per chunk, ordered by chunk number, with low visual noise and details on hover/click.
- The activity UI should appear in the running task core area and remain as a result summary after completion.
- Do not add manual TUI control buttons to the progress UI in the first version. Electron should show that TUI review is waiting, while Python keeps controlling TUI lifecycle.
- Show waiting duration and recent action for long-running external work. After 90 seconds, show a gentle still-waiting hint without marking failure.
- True failure state should come from process failure, caught command failure, or explicit chunk failure events.
- Keep detailed logs available in a collapsible section. Logs are for diagnosis, not the primary progress surface.
- Extract deep modules with small interfaces:
  - Progress event emitter: creates and emits structured command progress events from Python and logs the same payload.
  - Pipeline progress instrumentation: reports meaningful lifecycle events without exposing renderer concerns.
  - Desktop progress parser: consumes progress-prefixed stdout events alongside existing result events.
  - Renderer progress reducer: transforms progress events into timeline, current action, chunk activity, wait state, and summary.
  - Progress UI components: render timeline, current action, chunk activity, details, and collapsible logs from reducer output.
- Keep Electron command execution unchanged in spirit: Python CLI remains the source of truth, and Electron remains a client of emitted events.

## Testing Decisions

- Tests should verify external behavior: emitted progress events, reducer output, visible UI states, and command completion behavior. They should not depend on internal helper call order unless that helper is the public deep-module interface.
- Python tests should cover progress event serialization, stdout prefix emission, and run-log recording.
- Python tests should cover translation lifecycle events for local subtitle input: startup, input read, context generation, chunk planning, chunk rough/refine, quality, checkpoint, SRT write, and final completion.
- Python tests should cover URL input branch events: metadata lookup, reused subtitle, reused audio, download subtitle, audio extraction, and ASR fallback.
- Python tests should cover automatic repair and TUI review status events through fake review ports and fake clients.
- Python tests should cover chunk failure and warning states, including fallback-to-source behavior.
- Python tests should cover standalone `download`, `transcribe`, and `mux` command progress events at the command boundary.
- Renderer unit tests should cover the progress reducer with out-of-order concurrent chunk events.
- Renderer unit tests should cover state rollback from done to repairing and final warning.
- Renderer unit tests should cover skipped chunks and checkpoint-resumed chunks.
- Renderer unit tests should cover waiting-time derivation and the 90-second still-waiting hint.
- Electron smoke tests should verify that progress events from stdout update desktop state without breaking existing result event parsing.
- Electron E2E tests should verify that the main run area shows stage timeline, current action, chunk activity squares, chunk details, final result summary, and collapsible detailed logs.
- Existing desktop test commands remain the regression suite for build, lint, typecheck, smoke, and E2E.
- Existing Python unit tests remain the regression suite for pipeline behavior. Known optional ASR dependency failures should stay classified separately unless the dependency is installed or tests are explicitly adjusted.

## Out of Scope

- A global percentage progress bar.
- ETA prediction.
- Automatic stuck detection that marks slow work as failed.
- Manual TUI lifecycle control from Electron.
- Replacing the TUI review interface.
- Rewriting the Python translation pipeline around Electron-specific state.
- Parsing human-readable logs as the UI progress source.
- A separate progress trace directory or database.
- Cost estimation, billing display, or per-provider pricing.
- Full installer/package distribution changes.
- Remote telemetry or analytics for progress events.
- Persisting every historical progress event beyond the run log.

## Further Notes

- The design principle is “show what the program is doing, not a fake percentage.”
- The progress protocol should be treated like a small API contract between Python and Electron.
- The first implementation should favor simple, durable event names over exposing every internal method name.
- The chunk activity graph should make parallelism visible without making the app feel like a developer dashboard.
- This PRD intentionally keeps logs, LLM diagnostics, and progress as separate concepts: logs explain runtime behavior, LLM diagnostics explain model calls, and progress explains the user-facing task state.
