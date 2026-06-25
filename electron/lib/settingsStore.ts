import fs from "node:fs";
import path from "node:path";
import type { CredentialStatus, DesktopPreferences, DesktopSettings, ProviderDefinition } from "../types.js";
import { listProviders } from "./providerCatalog.js";

const SETTINGS_VERSION = 1;

export function defaultSettings(): DesktopSettings {
  return {
    version: SETTINGS_VERSION,
    apiKeys: {},
    preferences: {
      lastProviderId: "deepseek",
      modelsByProvider: {},
      customModelsByProvider: {},
    },
  };
}

function normalizeSettings(raw: unknown): DesktopSettings {
  const settings = defaultSettings();
  if (!raw || typeof raw !== "object") {
    return settings;
  }

  const record = raw as Partial<DesktopSettings>;
  settings.apiKeys = record.apiKeys && typeof record.apiKeys === "object" ? record.apiKeys : {};
  settings.preferences = {
    ...settings.preferences,
    ...(record.preferences && typeof record.preferences === "object" ? record.preferences : {}),
  };
  settings.preferences.modelsByProvider =
    settings.preferences.modelsByProvider && typeof settings.preferences.modelsByProvider === "object"
      ? settings.preferences.modelsByProvider
      : {};
  settings.preferences.customModelsByProvider =
    settings.preferences.customModelsByProvider && typeof settings.preferences.customModelsByProvider === "object"
      ? settings.preferences.customModelsByProvider
      : {};
  return settings;
}

export function readSettings(settingsPath: string): DesktopSettings {
  try {
    return normalizeSettings(JSON.parse(fs.readFileSync(settingsPath, "utf8")));
  } catch (error) {
    if (isNodeError(error) && error.code === "ENOENT") {
      return defaultSettings();
    }
    throw error;
  }
}

export function writeSettings(settingsPath: string, settings: DesktopSettings): void {
  fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
  fs.writeFileSync(settingsPath, JSON.stringify(normalizeSettings(settings), null, 2), "utf8");
}

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function maskSecret(secret: unknown): string {
  const value = cleanString(secret);
  if (!value) {
    return "";
  }
  return `...${value.slice(-4)}`;
}

export function credentialStatus(
  provider: ProviderDefinition,
  settings: DesktopSettings,
  env: NodeJS.ProcessEnv = process.env,
): CredentialStatus {
  const saved = cleanString(settings.apiKeys[provider.id]);
  if (saved) {
    return {
      source: "saved",
      label: `已保存 ${maskSecret(saved)}`,
      available: true,
      envKey: provider.envKey,
    };
  }

  const envValue = cleanString(env[provider.envKey]);
  if (envValue) {
    return {
      source: "env",
      label: `使用环境变量 ${provider.envKey}`,
      available: true,
      envKey: provider.envKey,
    };
  }

  return {
    source: "missing",
    label: "未配置",
    available: false,
    envKey: provider.envKey,
  };
}

export function summarizeSettings(settingsPath: string, env: NodeJS.ProcessEnv = process.env) {
  const settings = readSettings(settingsPath);
  const providers = listProviders().map((provider) =>
    Object.assign({}, provider, {
      credential: credentialStatus(provider, settings, env),
    }),
  );
  const firstAvailable = providers.find((provider) => provider.credential.available);
  const preferred =
    providers.find((provider) => provider.id === settings.preferences.lastProviderId && provider.credential.available) ||
    providers.find((provider) => provider.id === "deepseek" && provider.credential.available) ||
    firstAvailable ||
    providers.find((provider) => provider.id === "deepseek") ||
    providers[0];

  return {
    providers,
    preferences: settings.preferences,
    hasAnyCredential: providers.some((provider) => provider.credential.available),
    preferredProviderId: preferred ? preferred.id : "deepseek",
  };
}

export function saveApiKey(settingsPath: string, providerId: string, apiKey: string) {
  const key = cleanString(apiKey);
  if (!key) {
    throw new Error("API Key 不能为空");
  }
  const settings = readSettings(settingsPath);
  settings.apiKeys[providerId] = key;
  writeSettings(settingsPath, settings);
  return summarizeSettings(settingsPath);
}

export function clearApiKey(settingsPath: string, providerId: string) {
  const settings = readSettings(settingsPath);
  delete settings.apiKeys[providerId];
  writeSettings(settingsPath, settings);
  return summarizeSettings(settingsPath);
}

export function savePreferences(settingsPath: string, preferences: Partial<DesktopPreferences>) {
  const settings = readSettings(settingsPath);
  settings.preferences = {
    ...settings.preferences,
    ...(preferences && typeof preferences === "object" ? preferences : {}),
  };
  writeSettings(settingsPath, settings);
  return summarizeSettings(settingsPath);
}

export function resolveCredential(
  settingsPath: string,
  provider: ProviderDefinition,
  env: NodeJS.ProcessEnv = process.env,
) {
  const settings = readSettings(settingsPath);
  const saved = cleanString(settings.apiKeys[provider.id]);
  if (saved) {
    return {
      status: credentialStatus(provider, settings, env),
      envOverrides: {
        [provider.envKey]: saved,
      },
    };
  }

  const status = credentialStatus(provider, settings, env);
  if (status.available) {
    return {
      status,
      envOverrides: {},
    };
  }

  throw new Error(`${provider.name} API Key 未配置`);
}

export function savedCredentialEnvOverrides(settingsPath: string): Record<string, string> {
  const settings = readSettings(settingsPath);
  const overrides: Record<string, string> = {};
  for (const provider of listProviders()) {
    const saved = cleanString(settings.apiKeys[provider.id]);
    if (saved) {
      overrides[provider.envKey] = saved;
    }
  }
  return overrides;
}

function isNodeError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error;
}
