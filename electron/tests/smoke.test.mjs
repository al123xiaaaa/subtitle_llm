import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { buildEnv, buildPythonArgs } = require("../lib/cliCommands.cjs");
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

console.log("desktop smoke tests passed");
