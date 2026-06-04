const fs = require("node:fs");
const path = require("node:path");
const { listProviders } = require("./providerCatalog.cjs");

const SETTINGS_VERSION = 1;

function defaultSettings() {
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

function normalizeSettings(raw) {
  const settings = defaultSettings();
  if (!raw || typeof raw !== "object") {
    return settings;
  }
  settings.apiKeys = raw.apiKeys && typeof raw.apiKeys === "object" ? raw.apiKeys : {};
  settings.preferences = {
    ...settings.preferences,
    ...(raw.preferences && typeof raw.preferences === "object" ? raw.preferences : {}),
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

function readSettings(settingsPath) {
  try {
    return normalizeSettings(JSON.parse(fs.readFileSync(settingsPath, "utf8")));
  } catch (error) {
    if (error.code === "ENOENT") {
      return defaultSettings();
    }
    throw error;
  }
}

function writeSettings(settingsPath, settings) {
  fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
  fs.writeFileSync(settingsPath, JSON.stringify(normalizeSettings(settings), null, 2), "utf8");
}

function cleanString(value) {
  return typeof value === "string" ? value.trim() : "";
}

function maskSecret(secret) {
  const value = cleanString(secret);
  if (!value) {
    return "";
  }
  return `...${value.slice(-4)}`;
}

function credentialStatus(provider, settings, env = process.env) {
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

function summarizeSettings(settingsPath, env = process.env) {
  const settings = readSettings(settingsPath);
  const providers = listProviders().map((provider) => ({
    ...provider,
    credential: credentialStatus(provider, settings, env),
  }));
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

function saveApiKey(settingsPath, providerId, apiKey) {
  const key = cleanString(apiKey);
  if (!key) {
    throw new Error("API Key 不能为空");
  }
  const settings = readSettings(settingsPath);
  settings.apiKeys[providerId] = key;
  writeSettings(settingsPath, settings);
  return summarizeSettings(settingsPath);
}

function clearApiKey(settingsPath, providerId) {
  const settings = readSettings(settingsPath);
  delete settings.apiKeys[providerId];
  writeSettings(settingsPath, settings);
  return summarizeSettings(settingsPath);
}

function savePreferences(settingsPath, preferences) {
  const settings = readSettings(settingsPath);
  settings.preferences = {
    ...settings.preferences,
    ...(preferences && typeof preferences === "object" ? preferences : {}),
  };
  writeSettings(settingsPath, settings);
  return summarizeSettings(settingsPath);
}

function resolveCredential(settingsPath, provider, env = process.env) {
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

module.exports = {
  clearApiKey,
  credentialStatus,
  defaultSettings,
  maskSecret,
  readSettings,
  resolveCredential,
  saveApiKey,
  savePreferences,
  summarizeSettings,
  writeSettings,
};
