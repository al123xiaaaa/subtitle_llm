import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { _electron as electron } from "playwright";

const require = createRequire(import.meta.url);
const electronPath = require("electron");
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "../..");
const youtubeUrl = "https://www.youtube.com/watch?v=c0dm-l0AOBE&pp=ugUEEgJlbg%3D%3D";

const fakePythonSource = `#!/usr/bin/env node
const fs = require("node:fs");

const args = process.argv.slice(2);
const command = args[1] || "";
const commandLog = process.env.SUBTITLE_LLM_E2E_COMMAND_LOG;

if (args[0] === "-c") {
  console.log("[]");
  process.exit(0);
}

function optionValue(flag) {
  const index = args.indexOf(flag);
  return index >= 0 ? args[index + 1] : "";
}

function progress(event) {
  console.log("SUBTITLE_LLM_PROGRESS " + JSON.stringify({
    command,
    elapsed_ms: 10,
    ...event,
  }));
}

if (commandLog) {
  fs.appendFileSync(commandLog, JSON.stringify({
    args,
    command,
    env: {
      DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY || "",
      GEMINI_API_KEY: process.env.GEMINI_API_KEY || "",
      OPENAI_API_KEY: process.env.OPENAI_API_KEY || "",
    },
  }) + "\\n");
}

setTimeout(() => {
  if (command === "tasks") {
    console.log("[]");
    process.exit(0);
  }

  if (command === "translate") {
    const output = optionValue("--output") || "data/output/youtube.zh.srt";
    progress({
      stage: "startup",
      detail: "load_config",
      status: "done",
      label: "加载配置",
      message: "配置已加载",
    });
    progress({
      stage: "prepare_translation",
      detail: "plan_chunks",
      status: "done",
      label: "规划片段",
      message: "已规划 4 个片段",
      total_chunks: 4,
    });
    progress({
      stage: "processing_chunks",
      detail: "rough",
      status: "running",
      label: "初译",
      message: "正在初译第 2/4 个片段",
      total_chunks: 4,
      chunk: { index: 2, total: 4, status: "running", entry_start: 20, entry_end: 39, detail: "rough" },
      model: { provider: "deepseek", name: "deepseek-v4-pro" },
    });
    progress({
      stage: "processing_chunks",
      detail: "tui_warning",
      status: "warning",
      label: "带风险继续",
      message: "片段 2 复核达到上限，带风险继续",
      total_chunks: 4,
      chunk: {
        index: 2,
        total: 4,
        status: "warning",
        entry_start: 20,
        entry_end: 39,
        issue_summary: "仍有疑似缺失",
      },
      trace_id: "000002",
    });
    progress({
      stage: "processing_chunks",
      detail: "quality",
      status: "done",
      label: "质量检查",
      message: "片段 1/4 质量检查通过",
      total_chunks: 4,
      chunk: { index: 1, total: 4, status: "done", entry_start: 1, entry_end: 19 },
    });
    progress({
      stage: "generate_result",
      detail: "write_srt",
      status: "done",
      label: "写出字幕",
      message: "字幕已写出：" + output,
      total_chunks: 4,
    });
    console.log("\\n===== 翻译完成 =====");
    console.log("输出格式：source-first");
    console.log("SUBTITLE_LLM_RESULT " + JSON.stringify({
      command: "translate",
      output_file: output,
      source_video_file: args.includes("--embed-video") ? "data/input/youtube.mp4" : null,
      embedded_video_file: args.includes("--embed-video") ? "data/output/youtube.zh.mkv" : null,
      embedded_video_error: null,
      context_file: "data/output/youtube.zh_context.txt",
      task_id: "e2e-task-1",
      task_db_file: "data/user/translation-tasks.sqlite3",
      llm_trace_dir: "data/logs/e2e_translate_llm_trace",
      output_format: "source-first",
    }));
    console.log("日志文件：data/logs/e2e_translate.log");
    process.exit(0);
  }

  if (command === "mux") {
    const output = optionValue("--output") || "data/output/manual.zh.mkv";
    progress({
      stage: "mux",
      detail: "run_ffmpeg",
      status: "running",
      label: "执行 FFmpeg",
      message: "正在生成 MKV 软字幕视频",
    });
    progress({
      stage: "complete",
      detail: "complete",
      status: "done",
      label: "完成",
      message: "MKV 已生成：" + output,
    });
    console.log("===== MKV 生成完成 =====");
    console.log("SUBTITLE_LLM_RESULT " + JSON.stringify({ command: "mux", output_video_file: output }));
    console.log("日志文件：data/logs/e2e_mux.log");
    process.exit(0);
  }

  console.error("unknown command: " + command);
  process.exit(2);
}, 80);
`;

const fakeFfmpegSource = `#!/usr/bin/env node
if (process.argv.includes("-version")) {
  console.log("ffmpeg version e2e-fake");
  process.exit(0);
}
process.exit(0);
`;

const tests = [
  ["首次启动可以保存 DeepSeek API Key，且不泄露明文", testOnboardingSavesKey],
  ["YouTube URL 翻译会自动启用 MKV，并传递正确 CLI 参数", testYoutubeTranslateWithMkv],
  ["YouTube URL 可以勾选强制 ASR，不下载原字幕", testYoutubeTranslateForceAsr],
  ["翻译默认不二次润色，勾选后传递 refine 参数", testRefineToggle],
  ["已有字幕和视频可以单独生成 MKV", testManualMuxFlow],
  ["API Key 可以从环境变量回退，跳过首次配置", testEnvCredentialFallback],
  ["没有 FFmpeg 时 MKV 控件禁用但翻译表单仍可用", testFfmpegMissingDisablesMkvOnly],
];

await tests.reduce(async (previous, [name, test]) => {
  await previous;
  await test();
  console.log(`✓ ${name}`);
}, Promise.resolve());

async function testOnboardingSavesKey() {
  await withApp(async ({ page, userDataDir }) => {
    await page.locator("#onboarding").waitFor({ state: "visible" });
    await page.locator("#onboardingApiKey").fill("sk-e2e-deepseek");
    await page.locator("#onboardingSave").click();
    await page.locator("#onboarding").waitFor({ state: "hidden" });

    await page.locator('[data-tab="settings"]').click();
    await page.locator('[data-provider-card="deepseek"] .status-pill.is-saved').waitFor({ state: "visible" });

    const settings = JSON.parse(fs.readFileSync(path.join(userDataDir, "settings.json"), "utf8"));
    assert.equal(settings.apiKeys.deepseek, "sk-e2e-deepseek");
    assert.equal(await page.locator("body").evaluate((body) => body.innerText.includes("sk-e2e-deepseek")), false);
  });
}

async function testYoutubeTranslateWithMkv() {
  await withApp(async ({ page, commandLogPath, fakeFfmpegPath }) => {
    await saveOnboardingKey(page);
    await page.locator("#translateInput").fill(youtubeUrl);
    await page.locator("#embedMkv").waitFor({ state: "visible" });
    assert.equal(await page.locator("#embedMkv").isChecked(), true);
    assert.equal(await page.locator("#refineTranslation").isChecked(), false);
    assert.equal(await page.locator("#forceAsr").isChecked(), false);
    await page.locator("#modelSelect").selectOption("deepseek-v4-pro");

    await page.locator("#startTranslate").click();
    await waitForRunStatus(page, "完成");

    await page.locator("#progressCurrentMessage", { hasText: "任务已完成" }).waitFor();
    await page.locator("#chunkActivitySummary", { hasText: "4 个片段" }).waitFor();
    await page.locator("#chunkActivity .chunk-cell.is-warning").click();
    await page.locator("#chunkActivityDetail", { hasText: "仍有疑似缺失" }).waitFor();
    await page.locator("#subtitleResultPath", { hasText: "data/output/youtube.zh.srt" }).waitFor();
    await page.locator("#videoResultPath", { hasText: "data/output/youtube.zh.mkv" }).waitFor();
    await page.locator("#traceResultPath", { hasText: "data/logs/e2e_translate_llm_trace" }).waitFor();

    const commands = readCommands(commandLogPath).filter((entry) => entry.command === "translate");
    assert.equal(commands.length, 1);
    assert.deepEqual(commands[0].args.slice(0, 4), ["main.py", "translate", "--input", youtubeUrl]);
    assertHasArg(commands[0].args, "--target-language", "Chinese");
    assertHasArg(commands[0].args, "--source-language", "en");
    assertHasArg(commands[0].args, "--ffmpeg", fakeFfmpegPath);
    assert.equal(commands[0].args.includes("--embed-video"), true);
    assert.equal(commands[0].args.includes("--no-review"), true);
    assert.equal(commands[0].args.includes("--refine"), false);
    assert.equal(commands[0].args.includes("--force-asr"), false);
    assert.equal(commands[0].env.DEEPSEEK_API_KEY, "sk-e2e-deepseek");

    const configPath = valueAfter(commands[0].args, "--config");
    assert.ok(configPath, "expected generated config path");
    assert.match(fs.readFileSync(configPath, "utf8"), /model: "deepseek-v4-pro"/);
  });
}

async function testYoutubeTranslateForceAsr() {
  await withApp(async ({ page, commandLogPath }) => {
    await saveOnboardingKey(page);
    await page.locator("#translateInput").fill(youtubeUrl);
    await page.locator("#forceAsr").check();
    assert.equal(await page.locator("#forceAsr").isChecked(), true);

    await page.locator("#startTranslate").click();
    await waitForRunStatus(page, "完成");

    const commands = readCommands(commandLogPath).filter((entry) => entry.command === "translate");
    assert.equal(commands.length, 1);
    assert.equal(commands[0].args.includes("--force-asr"), true);
  });
}

async function testRefineToggle() {
  await withApp(async ({ page, commandLogPath }) => {
    await saveOnboardingKey(page);
    await page.locator("#translateInput").fill("input.srt");
    await page.locator("#refineTranslation").check();

    await page.locator("#startTranslate").click();
    await waitForRunStatus(page, "完成");

    const commands = readCommands(commandLogPath).filter((entry) => entry.command === "translate");
    assert.equal(commands.length, 1);
    assert.equal(commands[0].args.includes("--refine"), true);
  });
}

async function testManualMuxFlow() {
  await withApp({ env: { DEEPSEEK_API_KEY: "env-e2e-deepseek" } }, async ({ electronApp, page, tempDir, commandLogPath, fakeFfmpegPath }) => {
    const subtitlePath = path.join(tempDir, "translated.zh.srt");
    const videoPath = path.join(tempDir, "source.mp4");
    fs.writeFileSync(subtitlePath, "1\\n00:00:00,000 --> 00:00:01,000\\n你好\\n", "utf8");
    fs.writeFileSync(videoPath, "video", "utf8");

    await electronApp.evaluate(({ dialog }, paths) => {
      dialog.showOpenDialog = async (_window, options = {}) => {
        if (String(options.title || "").includes("已翻译字幕")) {
          return { canceled: false, filePaths: [paths.subtitlePath] };
        }
        if (String(options.title || "").includes("视频文件")) {
          return { canceled: false, filePaths: [paths.videoPath] };
        }
        return { canceled: true, filePaths: [] };
      };
    }, { subtitlePath, videoPath });

    await page.locator("#chooseMuxSubtitle").click();
    await page.locator("#chooseMuxVideo").click();
    await page.locator("#muxSubtitle").waitFor({ state: "visible" });
    assert.equal(await page.locator("#muxSubtitle").inputValue(), subtitlePath);
    assert.equal(await page.locator("#muxVideo").inputValue(), videoPath);
    assert.equal(await page.locator("#startMux").isEnabled(), true);

    await page.locator("#startMux").click();
    await waitForRunStatus(page, "完成");
    await page.locator("#videoResultPath", { hasText: "data/output/manual.zh.mkv" }).waitFor();

    const commands = readCommands(commandLogPath).filter((entry) => entry.command === "mux");
    assert.equal(commands.length, 1);
    assert.deepEqual(commands[0].args.slice(0, 4), ["main.py", "mux", videoPath, subtitlePath]);
    assertHasArg(commands[0].args, "--target-language", "Chinese");
    assertHasArg(commands[0].args, "--ffmpeg", fakeFfmpegPath);
  });
}

async function testEnvCredentialFallback() {
  await withApp({ env: { DEEPSEEK_API_KEY: "env-e2e-deepseek" } }, async ({ page, userDataDir }) => {
    await page.locator("#onboarding").waitFor({ state: "hidden" });
    await page.locator("#sidebarProviderStatus .status-pill.is-env", { hasText: "DEEPSEEK_API_KEY" }).waitFor();
    assert.equal(fs.existsSync(path.join(userDataDir, "settings.json")), false);
  });
}

async function testFfmpegMissingDisablesMkvOnly() {
  await withApp(
    {
      disableFfmpeg: true,
      env: { DEEPSEEK_API_KEY: "env-e2e-deepseek" },
    },
    async ({ page }) => {
      await page.locator("#mkvCapabilityStatus", { hasText: "未找到 FFmpeg" }).waitFor();
      assert.equal(await page.locator("#embedMkv").isDisabled(), true);
      assert.equal(await page.locator("#chooseTranslateVideo").isDisabled(), true);
      assert.equal(await page.locator("#startMux").isDisabled(), true);
      assert.equal(await page.locator("#startTranslate").isEnabled(), true);
    },
  );
}

async function withApp(optionsOrCallback, maybeCallback) {
  const options = typeof optionsOrCallback === "function" ? {} : optionsOrCallback;
  const callback = typeof optionsOrCallback === "function" ? optionsOrCallback : maybeCallback;
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "subtitle-llm-electron-e2e-"));
  const userDataDir = path.join(tempDir, "userData");
  const commandLogPath = path.join(tempDir, "commands.jsonl");
  const fakePythonPath = path.join(tempDir, process.platform === "win32" ? "fake-python.cmd" : "fake-python");
  const fakeFfmpegPath = path.join(tempDir, process.platform === "win32" ? "fake-ffmpeg.cmd" : "fake-ffmpeg");

  writeExecutable(fakePythonPath, fakePythonSource);
  writeExecutable(fakeFfmpegPath, fakeFfmpegSource);

  const env = {
    ...process.env,
    DEEPSEEK_API_KEY: "",
    GEMINI_API_KEY: "",
    OPENAI_API_KEY: "",
    ...options.env,
    ELECTRON_ENABLE_LOGGING: "0",
    SUBTITLE_LLM_USER_DATA_DIR: userDataDir,
    SUBTITLE_LLM_PYTHON: fakePythonPath,
    SUBTITLE_LLM_E2E_COMMAND_LOG: commandLogPath,
  };
  if (options.disableFfmpeg) {
    env.SUBTITLE_LLM_DISABLE_FFMPEG_DETECT = "1";
  } else {
    env.SUBTITLE_LLM_FFMPEG = fakeFfmpegPath;
  }

  const electronApp = await electron.launch({
    executablePath: electronPath,
    args: [projectRoot],
    cwd: projectRoot,
    env,
    timeout: 30000,
  });

  const consoleErrors = [];
  const pageErrors = [];

  try {
    const page = await electronApp.firstWindow();
    page.on("console", (message) => {
      if (message.type() === "error") {
        consoleErrors.push(message.text());
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await waitForAppReady(page);
    await callback({ electronApp, page, tempDir, userDataDir, commandLogPath, fakeFfmpegPath });
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(consoleErrors, []);
  } finally {
    await electronApp.close().catch(() => {});
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

async function waitForAppReady(page) {
  await page.locator("#translateForm").waitFor({ state: "visible" });
  await page.waitForFunction(() => document.querySelector("#runtimeInfo")?.textContent !== "加载中");
}

async function saveOnboardingKey(page) {
  await page.locator("#onboarding").waitFor({ state: "visible" });
  await page.locator("#onboardingApiKey").fill("sk-e2e-deepseek");
  await page.locator("#onboardingSave").click();
  await page.locator("#onboarding").waitFor({ state: "hidden" });
}

async function waitForRunStatus(page, expected) {
  await page.waitForFunction(
    (text) => document.querySelector("#runStatus")?.textContent === text,
    expected,
    { timeout: 10000 },
  );
}

function writeExecutable(filePath, source) {
  fs.writeFileSync(filePath, source, { encoding: "utf8", mode: 0o755 });
  if (process.platform !== "win32") {
    fs.chmodSync(filePath, 0o755);
  }
}

function readCommands(commandLogPath) {
  return fs
    .readFileSync(commandLogPath, "utf8")
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

function assertHasArg(args, flag, expectedValue) {
  assert.equal(valueAfter(args, flag), expectedValue, `expected ${flag} ${expectedValue} in ${args.join(" ")}`);
}

function valueAfter(args, flag) {
  const index = args.indexOf(flag);
  return index >= 0 ? args[index + 1] : "";
}
