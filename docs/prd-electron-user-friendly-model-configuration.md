## Problem Statement

Subtitle LLM 的桌面前端已经可以调用 Python 翻译管线，但当前模型配置暴露了 Provider、Endpoint、环境变量名、YAML 配置文件等开发者概念。普通用户想做的是“选择字幕文件并翻译”，却被迫理解 OpenAI-compatible API、模型 ID、环境变量和配置文件之间的关系。

这不符合用户操作心理：用户在第一次使用时需要先建立“我已经连接了一个翻译服务”的信心，而不是先完成一套工程配置。尤其是在使用 DeepSeek 时，用户期望看到清晰的服务商和模型选择，例如 DeepSeek V4 Flash 或 DeepSeek V4 Pro，而不是手动填写 provider、endpoint 和 env key。

## Solution

把桌面前端改为普通用户视角的服务商配置与翻译任务流程。

用户首次打开 App 时，如果没有任何可用 API Key，进入轻量首次配置向导。用户选择一个翻译服务商，默认推荐 DeepSeek，只需要填写对应 API Key。保存后进入翻译页。

翻译页只呈现本次任务需要理解的选择：输入文件、目标语言、翻译服务、模型、输出格式和开始翻译。服务商账号配置放在独立设置页长期管理。DeepSeek 默认使用 `deepseek-v4-flash`，同时允许用户方便选择 `deepseek-v4-pro` 或自定义模型 ID。

API Key 的优先级为：本地保存的 key > 系统环境变量 > 未配置提示。未配置当前服务商 key 时，不允许用户启动翻译，而是提供明确的设置入口。

## User Stories

1. As a subtitle translator, I want to open the desktop app and immediately understand how to start, so that I do not need to learn command-line configuration first.
2. As a first-time user, I want the app to ask me to choose a translation service only when no API Key is available, so that setup happens at the right moment.
3. As a first-time user, I want DeepSeek to be the recommended translation service, so that I can pick the suggested path without researching providers.
4. As a first-time DeepSeek user, I want to paste my DeepSeek API Key once, so that I do not need to re-enter it for every translation.
5. As a user with `DEEPSEEK_API_KEY` already set in my environment, I want the app to detect it automatically, so that I can start translating without extra setup.
6. As a user with saved API Keys, I want the app to skip the first-run setup, so that returning to the app feels direct.
7. As a user, I want a Settings page for API Keys, so that account-level configuration is separate from translation task choices.
8. As a user, I want Settings to show DeepSeek, Gemini, and OpenAI status, so that I know which services are ready.
9. As a user, I want to see whether a service is saved locally, using an environment variable, or not configured, so that I understand why translation is or is not available.
10. As a user, I want to replace a saved API Key, so that I can rotate keys without editing files.
11. As a user, I want to clear a saved API Key, so that I can remove credentials from this machine.
12. As a privacy-conscious user, I want saved keys to be masked in the UI, so that secret values are not casually exposed.
13. As a user, I want the app to avoid writing API Keys into YAML config files, logs, or project files, so that secrets stay out of generated artifacts.
14. As a translator, I want the translation page to have a service dropdown, so that choosing DeepSeek, Gemini, or OpenAI feels familiar.
15. As a translator, I want the model dropdown to update based on the selected service, so that I only see relevant models.
16. As a DeepSeek user, I want to choose DeepSeek V4 Flash, so that I can prioritize speed and cost for most subtitle work.
17. As a DeepSeek user, I want to choose DeepSeek V4 Pro, so that I can prioritize quality for difficult subtitles.
18. As an advanced DeepSeek user, I want to enter a custom model ID, so that I can use newly released or account-specific models.
19. As a user, I want DeepSeek V4 Flash selected by default, so that common subtitle translation starts from a fast recommended option.
20. As a user, I want old DeepSeek model names to be avoided in the main UI, so that I do not accidentally choose deprecated models.
21. As a user, I want the app to show a clear “not configured” state when the selected service lacks a key, so that I know what to fix.
22. As a user, I want Start Translation disabled when the selected service is not configured, so that I do not start a task that will immediately fail.
23. As a user, I want a “configure API Key” action next to the disabled start state, so that I can recover in one click.
24. As a user, I want the app to remember my last selected translation service, so that repeated use is faster.
25. As a user, I want the app to remember my last selected model per service, so that repeated work keeps my preferred speed or quality setting.
26. As a user, I want output format and review mode to remain available, so that the existing translation workflow remains useful.
27. As a user, I want YAML configuration to remain available as an advanced option, so that power users can still use custom provider settings.
28. As a user, I want advanced Provider, Endpoint, and environment-variable settings hidden by default, so that the main translation flow stays simple.
29. As a custom endpoint user, I want a Custom service path, so that I can use OpenAI-compatible endpoints without blocking the main user flow.
30. As a user, I want task logs to remain visible during translation, so that I can monitor progress.
31. As a user, I want generated model config to be an internal detail, so that I do not need to understand temporary YAML files.
32. As a developer maintaining the app, I want model catalog logic separated from renderer UI code, so that service/model choices can be tested and updated independently.
33. As a developer maintaining the app, I want credential resolution separated from command execution, so that key storage can later move to system keychain without changing translation flow.
34. As a developer maintaining the app, I want command construction to keep using the existing Python CLI contract, so that the desktop app does not fork the translation pipeline.
35. As a developer maintaining the app, I want external behavior tests for service selection and config generation, so that UI simplification does not break translation startup.

## Implementation Decisions

- Replace the current developer-oriented model form in the main translation view with a user-oriented service and model selection flow.
- Keep the Python translation pipeline as the source of truth. Electron should continue to launch the existing CLI command and pass configuration through the existing config contract.
- Introduce a first-run configuration state. It appears only when no supported service has a locally saved key and no supported environment variable is present.
- Add a persistent Settings view in the Electron UI for DeepSeek, Gemini, and OpenAI API Key management.
- Store local settings in Electron user data, not in the project directory and not in generated YAML.
- Use this credential resolution order: saved local key first, environment variable second, missing state third.
- Do not show raw API Keys after saving. Show only provider status and optionally a masked suffix.
- Do not write API Keys into logs, generated YAML, or task output.
- Generate runtime configuration internally from the selected service and model, then pass it to the existing CLI using the existing config option.
- DeepSeek is the recommended default service when multiple services are available.
- DeepSeek model choices in normal mode are `deepseek-v4-flash`, `deepseek-v4-pro`, and custom model ID.
- DeepSeek default model is `deepseek-v4-flash`.
- Gemini and OpenAI should receive the same service/model treatment, but their exact default model catalog can be updated independently.
- Model selection belongs on the translation page because it is task-level. API Key management belongs in Settings because it is account-level.
- Keep YAML config as an advanced path, but do not present YAML as the normal model-selection experience.
- Keep Custom provider as an advanced path for OpenAI-compatible endpoints.
- Disable Start Translation when the selected service is not configured.
- Provide a direct action from the disabled state to the relevant Settings section.
- Remember the last selected service and model in local settings.
- Extract deep modules with small interfaces:
  - Settings store: read, write, clear, and summarize provider credentials.
  - Provider catalog: list supported services, model options, defaults, environment variable names, and whether custom model IDs are allowed.
  - Credential resolver: resolve provider credential status from local settings and environment.
  - Runtime config builder: build the CLI-compatible config from provider, model, and resolved credential env.
  - Translation form state reducer: derive enabled states, warnings, and next actions from selected provider/model and credential status.
- Renderer UI should consume these modules through narrow IPC APIs rather than directly reading files or environment variables.

## Testing Decisions

- Tests should focus on external behavior and user-visible state rather than DOM implementation details.
- Test the settings store with read/write/clear behavior and ensure secrets are not returned in display summaries.
- Test credential resolution for saved key, environment variable fallback, and missing key.
- Test provider catalog defaults, especially DeepSeek defaulting to V4 Flash and exposing V4 Pro.
- Test runtime config generation for DeepSeek V4 Flash and V4 Pro.
- Test that generated config validates against the existing Python config schema.
- Test that API Keys do not appear in generated YAML, log event payloads, or display summaries.
- Test translation command construction still receives a config path and preserves existing input, output, target language, source language, resume, review mode, and output format behavior.
- Test form-state derivation: configured provider enables Start Translation, missing provider disables Start Translation and produces a configure action.
- Test custom model selection: selecting custom reveals model ID input and uses that ID in generated config.
- Reuse the existing desktop smoke-test style for Node-side command/config behavior.
- Continue using existing Python unittest coverage as a regression safety net for CLI and config validation.
- Browser or Electron smoke testing should verify that service/model selectors are clickable and not overlapped by the log panel.

## Out of Scope

- Full packaged installer distribution.
- System keychain or encrypted credential storage in the first implementation.
- OAuth or hosted account login for any provider.
- Automatic online model discovery from provider APIs.
- Pricing display, token cost estimation, or provider billing integration.
- DeepSeek thinking-mode controls such as reasoning effort.
- Rewriting the Python translation pipeline.
- Replacing YAML support for advanced users.
- Supporting every OpenAI-compatible provider in normal mode.
- Multi-user credential profiles.

## Further Notes

- DeepSeek current main model names are `deepseek-v4-flash` and `deepseek-v4-pro`; older names such as `deepseek-chat` and `deepseek-reasoner` should not be the primary UI choices.
- The current UI already demonstrated the main failure mode: developer concepts leaked into the normal translation path. This PRD intentionally separates account setup, model choice, and task execution.
- The first version may store credentials in a local settings file, but the storage layer should be abstract enough to move to system keychain later.
- The product principle for this work is: first-time successful translation should require the fewest possible conceptual steps.
