import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { buildEnv, buildPythonArgs } from "../../dist/electron/lib/cliCommands.js";
import { createDesktopRuntime } from "../../dist/electron/lib/desktopRuntime.js";
import { createFfmpegDetector } from "../../dist/electron/lib/ffmpegStatus.js";
import { buildDesktopModelConfigContent } from "../../dist/electron/lib/modelConfig.js";
import {
  applyProgressEvent,
  chunkSummary,
  createInitialJobProgressState,
  finishProgress,
  selectProgressChunk,
  selectedChunk,
  startProgress,
} from "../../dist/electron/lib/progressModel.js";
import { getProvider, listProviders, resolveModelId } from "../../dist/electron/lib/providerCatalog.js";
import {
  clearApiKey,
  readSettings,
  resolveCredential,
  saveApiKey,
  savePreferences,
  summarizeSettings,
} from "../../dist/electron/lib/settingsStore.js";

assert.deepEqual(
  buildPythonArgs({
    command: "translate",
    options: {
      input: "input.srt",
      targetLanguage: "Chinese",
      sourceLanguage: "en",
      output: "output.srt",
      outputFormat: "source-first",
      resume: true,
      reviewMode: "auto",
      embedVideo: true,
      video: "video.mp4",
      ffmpeg: "/usr/local/bin/ffmpeg",
    },
  }),
  [
    "main.py",
    "translate",
    "--input",
    "input.srt",
    "--target-language",
    "Chinese",
    "--output",
    "output.srt",
    "--source-language",
    "en",
    "--format",
    "source-first",
    "--resume",
    "--no-review",
    "--embed-video",
    "--video",
    "video.mp4",
    "--ffmpeg",
    "/usr/local/bin/ffmpeg",
  ],
);

assert.deepEqual(
  buildPythonArgs({
    command: "download",
    options: {
      url: "https://example.com/video",
      outputDir: "data/input",
      sourceLanguage: "en",
    },
  }),
  ["main.py", "download", "https://example.com/video", "--output-dir", "data/input", "--source-language", "en"],
);

assert.deepEqual(
  buildPythonArgs({
    command: "mux",
    options: {
      video: "video.mp4",
      subtitle: "subtitle.srt",
      targetLanguage: "Chinese",
      ffmpeg: "/usr/local/bin/ffmpeg",
    },
  }),
  [
    "main.py",
    "mux",
    "video.mp4",
    "subtitle.srt",
    "--target-language",
    "Chinese",
    "--ffmpeg",
    "/usr/local/bin/ffmpeg",
  ],
);

assert.deepEqual(
  buildPythonArgs({
    command: "transcribe",
    options: {
      audio: "audio.wav",
      output: "audio.srt",
      language: "English",
      config: "custom.yaml",
    },
  }),
  ["main.py", "transcribe", "audio.wav", "--output", "audio.srt", "--language", "English", "--config", "custom.yaml"],
);

assert.throws(() => buildPythonArgs({ command: "translate", options: { input: "", targetLanguage: "Chinese" } }), /不能为空/);
assert.throws(() => buildPythonArgs({ command: "mux", options: { video: "", subtitle: "subtitle.srt" } }), /不能为空/);
assert.throws(
  () => buildPythonArgs({ command: "translate", options: { input: "x.srt", targetLanguage: "Chinese", outputFormat: "bad" } }),
  /不支持的输出格式/,
);
assert.equal(buildEnv({}, { DEEPSEEK_API_KEY: "secret" }).DEEPSEEK_API_KEY, "secret");
assert.throws(() => buildEnv({}, { "BAD-NAME": "secret" }), /环境变量名无效/);

const providers = listProviders();
assert.equal(getProvider("deepseek").defaultModel, "deepseek-v4-flash");
assert.equal(resolveModelId(getProvider("deepseek"), "deepseek-v4-pro", ""), "deepseek-v4-pro");
assert.throws(() => resolveModelId(getProvider("deepseek"), "__custom__", ""), /不能为空/);
assert.ok(providers.some((provider) => provider.id === "gemini"));

const deepseekConfig = buildDesktopModelConfigContent({
  mode: "service",
  providerId: "deepseek",
  modelId: "deepseek-v4-flash",
});
assert.match(deepseekConfig, /provider: "openai"/);
assert.match(deepseekConfig, /api_key_env: "DEEPSEEK_API_KEY"/);
assert.match(deepseekConfig, /endpoint: "https:\/\/api\.deepseek\.com"/);
assert.match(deepseekConfig, /model: "deepseek-v4-flash"/);
assert.doesNotMatch(deepseekConfig, /sk-test-secret/);

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "subtitle-llm-settings-"));
const settingsPath = path.join(tempDir, "settings.json");
assert.equal(summarizeSettings(settingsPath, {}).hasAnyCredential, false);
saveApiKey(settingsPath, "deepseek", "sk-test-secret");
assert.equal(readSettings(settingsPath).apiKeys.deepseek, "sk-test-secret");
let summary = summarizeSettings(settingsPath, {});
assert.equal(summary.hasAnyCredential, true);
assert.equal(summary.providers.find((provider) => provider.id === "deepseek").credential.source, "saved");
assert.match(summary.providers.find((provider) => provider.id === "deepseek").credential.label, /secret|cret|ret$/);
let credential = resolveCredential(settingsPath, getProvider("deepseek"), {});
assert.equal(credential.envOverrides.DEEPSEEK_API_KEY, "sk-test-secret");
clearApiKey(settingsPath, "deepseek");
summary = summarizeSettings(settingsPath, { DEEPSEEK_API_KEY: "env-secret" });
assert.equal(summary.providers.find((provider) => provider.id === "deepseek").credential.source, "env");
credential = resolveCredential(settingsPath, getProvider("deepseek"), { DEEPSEEK_API_KEY: "env-secret" });
assert.deepEqual(credential.envOverrides, {});
savePreferences(settingsPath, {
  lastProviderId: "deepseek",
  modelsByProvider: { deepseek: "deepseek-v4-pro" },
});
assert.equal(readSettings(settingsPath).preferences.modelsByProvider.deepseek, "deepseek-v4-pro");

let ffmpegChecks = 0;
const detectFfmpeg = createFfmpegDetector({
  env: { SUBTITLE_LLM_FFMPEG: "/tmp/fake-ffmpeg" },
  fileSystem: { existsSync: (filePath) => filePath === "/tmp/fake-ffmpeg" },
  pathModule: path,
  spawnSyncFn: (command) => {
    ffmpegChecks += 1;
    assert.equal(command, "/tmp/fake-ffmpeg");
    return { status: 0, stdout: "ffmpeg version fake\n" };
  },
});
assert.equal(detectFfmpeg().available, true);
assert.equal(detectFfmpeg().executable, "/tmp/fake-ffmpeg");
assert.equal(ffmpegChecks, 1);
assert.equal(createFfmpegDetector({ env: { SUBTITLE_LLM_DISABLE_FFMPEG_DETECT: "1" } })().available, false);

let progress = startProgress("translate", 1000);
progress = applyProgressEvent(
  progress,
  {
    command: "translate",
    stage: "prepare_translation",
    detail: "plan_chunks",
    status: "done",
    label: "规划片段",
    message: "已规划 3 个片段",
    total_chunks: 3,
  },
  1100,
);
assert.equal(progress.chunks.length, 3);
assert.equal(progress.chunks[0].status, "waiting");
progress = applyProgressEvent(
  progress,
  {
    command: "translate",
    stage: "processing_chunks",
    detail: "quality",
    status: "done",
    label: "质量检查",
    message: "片段 2 通过",
    chunk: { index: 2, total: 3, status: "done", entry_start: 10, entry_end: 20 },
  },
  1200,
);
progress = applyProgressEvent(
  progress,
  {
    command: "translate",
    stage: "processing_chunks",
    detail: "drift",
    status: "running",
    label: "对齐漂移重译",
    message: "片段 2 回退到重译",
    chunk: { index: 2, total: 3, status: "repairing" },
  },
  1300,
);
assert.equal(progress.chunks[1].status, "repairing");
progress = applyProgressEvent(
  progress,
  {
    command: "translate",
    stage: "processing_chunks",
    detail: "tui_warning",
    status: "warning",
    label: "带风险继续",
    message: "片段 2 带风险继续",
    chunk: { index: 2, total: 3, status: "warning", issue_summary: "仍有疑似缺失" },
  },
  1400,
);
progress = selectProgressChunk(progress, 2);
assert.equal(selectedChunk(progress).issueSummary, "仍有疑似缺失");
assert.match(chunkSummary(progress.chunks), /1 个带风险/);
assert.equal(finishProgress(progress, true, 1500).currentStatus, "done");
assert.equal(createInitialJobProgressState().currentLabel, "等待任务");

const runtimeProjectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "subtitle-llm-runtime-"));
const runtimeUserData = path.join(runtimeProjectRoot, "userData");
fs.writeFileSync(path.join(runtimeProjectRoot, "main.py"), "", "utf8");
const runtime = createDesktopRuntime({
  app: { getPath: () => runtimeUserData },
  projectRoot: runtimeProjectRoot,
  env: { SUBTITLE_LLM_PYTHON: "python-e2e" },
  detectFfmpeg: () => ({ available: true, executable: "/tmp/fake-ffmpeg", version: "fake", error: "" }),
});
assert.equal(runtime.getAppState().pythonExecutable, "python-e2e");
assert.equal(runtime.getAppState().ffmpeg.executable, "/tmp/fake-ffmpeg");
assert.equal(runtime.saveProviderApiKey("deepseek", "sk-runtime").hasAnyCredential, true);
assert.equal(runtime.clearProviderApiKey("deepseek").hasAnyCredential, false);
assert.equal(runtime.resolveUserPath("data/output/demo.srt"), path.join(runtimeProjectRoot, "data/output/demo.srt"));

let missingDependencySpawnCalled = false;
const missingDependencyRuntime = createDesktopRuntime({
  app: { getPath: () => runtimeUserData },
  projectRoot: runtimeProjectRoot,
  env: { SUBTITLE_LLM_PYTHON: "python-e2e" },
  spawnFn: () => {
    missingDependencySpawnCalled = true;
    throw new Error("should not spawn job when dependencies are missing");
  },
  spawnSyncFn: () => ({ status: 1, stdout: '["pysubs2"]', stderr: "", output: [], pid: 0, signal: null }),
  detectFfmpeg: () => ({ available: true, executable: "/tmp/fake-ffmpeg", version: "fake", error: "" }),
});
assert.throws(
  () => missingDependencyRuntime.startJob(
    { isDestroyed: () => false, send: () => {} },
    { command: "translate", options: { input: "input.srt", targetLanguage: "Chinese" } },
  ),
  /Python 环境缺少依赖：pysubs2/,
);
assert.equal(missingDependencySpawnCalled, false);

console.log("desktop smoke tests passed");
