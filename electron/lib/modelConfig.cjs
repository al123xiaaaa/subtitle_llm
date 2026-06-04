const fs = require("node:fs");
const path = require("node:path");
const { getProvider, resolveModelId } = require("./providerCatalog.cjs");

function yamlString(value) {
  return JSON.stringify(value);
}

function cleanString(value) {
  return typeof value === "string" ? value.trim() : "";
}

function renderModelSection(name, model, defaults = {}) {
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

function buildModelFromSelection(selection = {}) {
  const provider = getProvider(selection.providerId);
  const model = resolveModelId(provider, selection.modelId, selection.customModelId);

  return {
    provider: provider.configProvider,
    model,
    apiKeyEnv: provider.envKey,
    endpoint: cleanString(selection.endpoint) || provider.endpoint,
  };
}

function buildDesktopModelConfigContent(selection = {}) {
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
  ].join("\n");
}

function writeDesktopModelConfig(projectRoot, selection) {
  const outputDir = path.join(projectRoot, "data", "desktop-configs");
  fs.mkdirSync(outputDir, { recursive: true });

  const outputPath = path.join(outputDir, "latest-model-config.yaml");
  const content = buildDesktopModelConfigContent(selection);
  fs.writeFileSync(outputPath, content, "utf8");
  return outputPath;
}

module.exports = {
  buildDesktopModelConfigContent,
  buildModelFromSelection,
  writeDesktopModelConfig,
};
