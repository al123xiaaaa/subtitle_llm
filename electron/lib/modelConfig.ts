import fs from "node:fs";
import path from "node:path";
import type { ModelSelection } from "../types.js";
import { getProvider, resolveModelId } from "./providerCatalog.js";

interface DesktopModelConfig {
  provider: string;
  model: string;
  apiKeyEnv: string;
  endpoint: string;
}

function yamlString(value: string): string {
  return JSON.stringify(value);
}

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function renderModelSection(name: string, model: DesktopModelConfig, defaults: Record<string, string> = {}): string {
  const lines = [
    `${name}:`,
    `  provider: ${yamlString(model.provider)}`,
    `  api_key_env: ${yamlString(model.apiKeyEnv)}`,
    `  model: ${yamlString(model.model)}`,
  ];

  if (model.endpoint) {
    lines.push(`  endpoint: ${yamlString(model.endpoint)}`);
  }

  for (const [key, value] of Object.entries(defaults)) {
    lines.push(`  ${key}: ${value}`);
  }

  return lines.join("\n");
}

export function buildModelFromSelection(selection: Partial<ModelSelection> = {}): DesktopModelConfig {
  const provider = getProvider(selection.providerId);
  const model = resolveModelId(provider, selection.modelId, selection.customModelId);

  return {
    provider: provider.configProvider,
    model,
    apiKeyEnv: provider.envKey,
    endpoint: cleanString(selection.endpoint) || provider.endpoint,
  };
}

export function buildDesktopModelConfigContent(selection: Partial<ModelSelection> = {}): string {
  if (selection.mode !== "service") {
    throw new Error("未启用服务商模型配置");
  }

  const translationModel = buildModelFromSelection(selection);
  const summaryModel = translationModel;

  return [
    'config_version: "2"',
    'default_output_format: "source-first"',
    "",
    renderModelSection("summary_model", summaryModel, {
      temperature: "0.5",
      top_p: "0.85",
      top_k: "12",
      retry_delay_seconds: "40",
    }),
    "",
    renderModelSection("translation_model", translationModel, {
      temperature: "0.3",
      top_p: "0.8",
      max_tokens: "4096",
      rate_limit: "5",
      max_retries: "3",
      retry_delay_seconds: "10",
    }),
    "",
    "pipeline:",
    "  refine_translation: false",
    "",
  ].join("\n");
}

export function writeDesktopModelConfig(projectRoot: string, selection: ModelSelection): string {
  const outputDir = path.join(projectRoot, "data", "desktop-configs");
  fs.mkdirSync(outputDir, { recursive: true });

  const outputPath = path.join(outputDir, "latest-model-config.yaml");
  const content = buildDesktopModelConfigContent(selection) + renderAsrSection(projectRoot);
  fs.writeFileSync(outputPath, content, "utf8");
  return outputPath;
}

// 渲染 FunASR Python SDK 配置段（见 docs/adr/0003）。
// 不再依赖二进制路径；funasr 通过 pip 安装，模型首次运行自动下载。
function renderAsrSection(_projectRoot: string): string {
  return [
    "",
    "# FunASR Python SDK（由 modelConfig.ts 生成）。pip install funasr；模型首次自动下载。",
    "asr:",
    '  model_name: "FunAudioLLM/SenseVoiceSmall"',
    '  punc_model: "ct-punc"',
    '  spk_model: "cam++"',
    "  max_single_segment_time: 8000",
    '  device: "cpu"',
    '  hub: "hf"',
    "  trust_remote_code: true",
    "",
  ].join("\n");
}
