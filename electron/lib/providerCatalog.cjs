const PROVIDERS = [
  {
    id: "deepseek",
    name: "DeepSeek",
    envKey: "DEEPSEEK_API_KEY",
    configProvider: "openai",
    endpoint: "https://api.deepseek.com",
    defaultModel: "deepseek-v4-flash",
    models: [
      {
        id: "deepseek-v4-flash",
        label: "V4 Flash",
        description: "快速推荐",
      },
      {
        id: "deepseek-v4-pro",
        label: "V4 Pro",
        description: "质量优先",
      },
      {
        id: "__custom__",
        label: "自定义模型 ID",
        description: "手动输入",
      },
    ],
  },
  {
    id: "gemini",
    name: "Gemini",
    envKey: "GEMINI_API_KEY",
    configProvider: "gemini",
    endpoint: "",
    defaultModel: "gemini-3.1-flash-lite-preview",
    models: [
      {
        id: "gemini-3.1-flash-lite-preview",
        label: "Gemini Flash Lite",
        description: "项目默认",
      },
      {
        id: "__custom__",
        label: "自定义模型 ID",
        description: "手动输入",
      },
    ],
  },
  {
    id: "openai",
    name: "OpenAI",
    envKey: "OPENAI_API_KEY",
    configProvider: "openai",
    endpoint: "",
    defaultModel: "gpt-4.1-mini",
    models: [
      {
        id: "gpt-4.1-mini",
        label: "GPT-4.1 Mini",
        description: "默认推荐",
      },
      {
        id: "__custom__",
        label: "自定义模型 ID",
        description: "手动输入",
      },
    ],
  },
];

const PROVIDER_BY_ID = new Map(PROVIDERS.map((provider) => [provider.id, provider]));

function listProviders() {
  return PROVIDERS.map((provider) => ({
    ...provider,
    models: provider.models.map((model) => ({ ...model })),
  }));
}

function getProvider(providerId) {
  const provider = PROVIDER_BY_ID.get(providerId);
  if (!provider) {
    throw new Error(`未知翻译服务：${providerId || ""}`);
  }
  return provider;
}

function resolveModelId(provider, modelId, customModelId) {
  if (modelId === "__custom__") {
    const custom = typeof customModelId === "string" ? customModelId.trim() : "";
    if (!custom) {
      throw new Error(`${provider.name} 自定义模型 ID 不能为空`);
    }
    return custom;
  }

  const selected = modelId || provider.defaultModel;
  if (!provider.models.some((model) => model.id === selected)) {
    throw new Error(`${provider.name} 不支持模型：${selected}`);
  }
  return selected;
}

module.exports = {
  getProvider,
  listProviders,
  resolveModelId,
};
