import type { ModelSelection, ProviderDefinition, ProviderModel } from "../types.js";

export const PROVIDERS: ProviderDefinition[] = [
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
    id: "cliproxy",
    name: "CLIProxyAPI（本地）",
    envKey: "CLIPROXY_API_KEY",
    configProvider: "openai",
    endpoint: "http://127.0.0.1:8317/v1",
    defaultModel: "kimi-k2.5",
    // 模型列表通过 /v1/models 动态拉取，下面仅作拉取失败时的兜底
    dynamicModels: true,
    models: [
      {
        id: "kimi-k2.5",
        label: "Kimi K2.5",
        description: "本地代理 · 快速推荐",
      },
      {
        id: "kimi-k2.6",
        label: "Kimi K2.6",
        description: "本地代理 · 质量优先",
      },
      {
        id: "kimi-k3",
        label: "Kimi K3",
        description: "本地代理",
      },
      {
        id: "gpt-5.5",
        label: "GPT-5.5",
        description: "本地代理",
      },
      {
        id: "claude-sonnet-4-6",
        label: "Claude Sonnet 4.6",
        description: "本地代理",
      },
      {
        id: "gemini-3-flash",
        label: "Gemini 3 Flash",
        description: "本地代理",
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

export function listProviders(): ProviderDefinition[] {
  return PROVIDERS.map((provider) =>
    Object.assign({}, provider, {
      models: provider.models.map((model) => Object.assign({}, model)),
    }),
  );
}

export function getProvider(providerId: string | undefined): ProviderDefinition {
  const provider = PROVIDER_BY_ID.get(providerId || "");
  if (!provider) {
    throw new Error(`未知翻译服务：${providerId || ""}`);
  }
  return provider;
}

export function resolveModelId(provider: ProviderDefinition, modelId?: string, customModelId?: string): string {
  if (modelId === "__custom__") {
    const custom = cleanString(customModelId);
    if (!custom) {
      throw new Error(`${provider.name} 自定义模型 ID 不能为空`);
    }
    return custom;
  }

  const selected = modelId || provider.defaultModel;
  // 动态模型服务（如 CLIProxyAPI）的列表在渲染端拉取，主进程不校验具体 ID
  if (provider.dynamicModels) {
    return selected;
  }
  if (!provider.models.some((model) => model.id === selected)) {
    throw new Error(`${provider.name} 不支持模型：${selected}`);
  }
  return selected;
}

// 非对话类模型（绘图/语音/向量化等）不适合翻译，从动态列表过滤
const NON_CHAT_MODEL_PATTERN = /image|tts|embedding|whisper|moderation|speech|dall/i;

export function modelsFromApiResponse(payload: unknown): ProviderModel[] {
  const data = (payload as { data?: unknown } | null)?.data;
  if (!Array.isArray(data)) {
    return [];
  }
  const ids = new Set<string>();
  for (const entry of data) {
    const id = cleanString((entry as { id?: unknown } | null)?.id);
    if (id && !NON_CHAT_MODEL_PATTERN.test(id)) {
      ids.add(id);
    }
  }
  return Array.from(ids)
    .toSorted((a, b) => a.localeCompare(b))
    .map((id) => ({ id, label: id, description: "本地代理" }));
}

export function providerFromSelection(selection: ModelSelection): ProviderDefinition {
  return getProvider(selection.providerId);
}

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}
