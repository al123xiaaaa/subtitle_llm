import fs from "node:fs";
import path from "node:path";
import type { ModelSelection } from "../types.js";
import { getProvider, resolveModelId } from "./providerCatalog.js";
import { desktopContract } from "./desktopContract.js";

// 模型参数默认值、provider 覆盖、ASR 段全部来自 desktop-contract.json
// （Python 侧 settings.py 的 ASR 默认值由 tests/test_config_contract.py 锁定一致）。
// CLIProxyAPI 上游的 Kimi 思考模型仅允许 temperature=1、top_p=0.95，
// 故契约里 cliproxy 固定覆盖这组值（对已验证的 claude / gemini / gpt 同样可用）。
const MODEL_PARAMS = desktopContract.modelParams;
const PROVIDER_PARAM_OVERRIDES = desktopContract.providerParamOverrides;

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
  const providerOverrides = PROVIDER_PARAM_OVERRIDES[selection.providerId || ""] || {};

  return [
    'config_version: "2"',
    'default_output_format: "source-first"',
    "",
    renderModelSection("summary_model", summaryModel, {
      ...MODEL_PARAMS.summary,
      ...providerOverrides.summary,
    }),
    "",
    renderModelSection("translation_model", translationModel, {
      ...MODEL_PARAMS.translation,
      ...providerOverrides.translation,
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
// 字段取自契约，与 settings.py 的 ASRConfig 默认值保持一致（由测试锁定）。
function renderAsrSection(_projectRoot: string): string {
  const asr = desktopContract.asr;
  const lines = [
    "",
    "# FunASR Python SDK（由 desktop-contract.json 生成）。pip install funasr；模型首次自动下载。",
    "asr:",
    `  model_name: ${yamlString(asr.model_name)}`,
  ];
  if (asr.punc_model !== null) {
    lines.push(`  punc_model: ${yamlString(asr.punc_model)}`);
  }
  if (asr.spk_model !== null) {
    lines.push(`  spk_model: ${yamlString(asr.spk_model)}`);
  }
  lines.push(`  max_single_segment_time: ${asr.max_single_segment_time}`);
  lines.push(`  device: ${yamlString(asr.device)}`);
  lines.push(`  hub: ${yamlString(asr.hub)}`);
  lines.push(`  trust_remote_code: ${asr.trust_remote_code}`);
  if (asr.forced_aligner !== null) {
    lines.push(`  forced_aligner: ${yamlString(asr.forced_aligner)}`);
  }
  lines.push("");
  return lines.join("\n");
}
