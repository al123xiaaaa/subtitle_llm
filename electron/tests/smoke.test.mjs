import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { buildEnv, buildPythonArgs } = require("../lib/cliCommands.cjs");
const { createDesktopRuntime } = require("../lib/desktopRuntime.cjs");
const { createFfmpegDetector } = require("../lib/ffmpegStatus.cjs");
const { getProvider, listProviders, resolveModelId } = require("../lib/providerCatalog.cjs");
const { buildDesktopModelConfigContent } = require("../lib/modelConfig.cjs");
const {
  clearApiKey,
  readSettings,
  resolveCredential,
  saveApiKey,
  savePreferences,
  summarizeSettings,
} = require("../lib/settingsStore.cjs");

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

console.log("desktop smoke tests passed");
