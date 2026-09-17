import { computed, nextTick, reactive, ref } from "vue";
import type { AppState, ModelSelection, ProviderModel, ProviderSummary } from "../../../types";
import type { TaskTab } from "./controllerTypes";
import { cleanString, statusPillClass } from "./controllerUtils";
import { appRuntime } from "../effect/runtime";
import {
  clearSettingsKey as clearSettingsKeyProgram,
  loadDynamicModels as loadDynamicModelsProgram,
  persistProviderPreference as persistProviderPreferenceProgram,
  saveOnboardingKey as saveOnboardingKeyProgram,
  saveSettingsKey as saveSettingsKeyProgram,
} from "../effect/programs/providers";
import type { ProviderPorts } from "../effect/programs/providers";
import { parseTranslationMaxTokens } from "../../../lib/outputBudget";

interface ProviderSettingsOptions {
  appendLog: (text: string, kind?: "stdout" | "stderr") => void;
  getUseYamlConfig: () => boolean;
  setActiveTab: (tab: TaskTab) => void;
}

export function useProviderSettings(options: ProviderSettingsOptions) {
  const appState = ref<AppState | null>(null);
  const selectedProviderId = ref("deepseek");
  const selectedModelId = ref("");
  const customModelInput = ref("");
  const translationMaxTokensInput = ref<string | number>("");
  const outputBudgetBadInput = ref(false);
  const outputBudgetError = computed(() => {
    try {
      if (outputBudgetBadInput.value) {
        throw new Error("翻译输出上限必须为正整数，留空使用服务商默认值");
      }
      parseTranslationMaxTokens(translationMaxTokensInput.value);
      return "";
    } catch (error) {
      return error instanceof Error ? error.message : String(error);
    }
  });

  function onOutputBudgetInput(event: Event): void {
    outputBudgetBadInput.value = (event.target as HTMLInputElement).validity.badInput;
  }

  const selectedOnboardingProviderId = ref("deepseek");
  const onboardingApiKey = ref("");
  const onboardingDismissed = ref(false);
  const apiKeyDrafts = reactive<Record<string, string>>({});
  const dynamicModelsByProvider = reactive<Record<string, ProviderModel[]>>({});

  const providers = computed(() => appState.value?.providers || []);
  const selectedProvider = computed(() => providerById(selectedProviderId.value));
  const providerModels = computed(() => modelsForProvider(selectedProvider.value));
  const showCustomModelInput = computed(() => selectedModelId.value === "__custom__");
  const ffmpegAvailable = computed(() => Boolean(appState.value?.ffmpeg?.available));
  const runtimeInfo = computed(() => {
    if (!appState.value) {
      return "加载中";
    }
    return appState.value.hasMainPy ? "Python 环境就绪" : "未找到 main.py";
  });
  const visibleOnboardingProviders = computed(() =>
    providers.value.filter((provider) => ["deepseek", "gemini", "openai", "cliproxy"].includes(provider.id)),
  );
  const selectedOnboardingProvider = computed(() => providerById(selectedOnboardingProviderId.value));
  const showOnboarding = computed(() => Boolean(appState.value && !appState.value.hasAnyCredential && !onboardingDismissed.value));
  const onboardingKeyLabel = computed(() =>
    selectedOnboardingProvider.value ? `${selectedOnboardingProvider.value.name} API Key` : "API Key",
  );
  const providerCredentialStatus = computed(() => {
    if (options.getUseYamlConfig()) {
      return { text: "使用 YAML 配置", className: "status-pill is-env" };
    }
    const status = selectedProvider.value?.credential;
    if (!status) {
      return { text: "未加载", className: "status-pill is-missing" };
    }
    return { text: status.label, className: statusPillClass(status) };
  });
  const configureProviderText = computed(() => (selectedProvider.value ? "去配置" : "配置 API Key"));
  const showConfigureProvider = computed(
    () => !options.getUseYamlConfig() && Boolean(selectedProvider.value) && !selectedProvider.value?.credential.available,
  );

  function providerById(providerId: string): ProviderSummary | null {
    return providers.value.find((provider) => provider.id === providerId) || providers.value[0] || null;
  }

  // 动态模型服务优先用拉取到的列表，静态目录仅作兜底；始终保留「自定义模型 ID」入口
  function modelsForProvider(provider: ProviderSummary | null): ProviderModel[] {
    if (!provider) {
      return [];
    }
    if (provider.dynamicModels && dynamicModelsByProvider[provider.id]?.length) {
      const customEntries = provider.models.filter((model) => model.id === "__custom__");
      return [...dynamicModelsByProvider[provider.id], ...customEntries];
    }
    return provider.models || [];
  }

  function selectedModelForProvider(provider: ProviderSummary): string {
    const models = modelsForProvider(provider);
    const stored = appState.value?.preferences?.modelsByProvider?.[provider.id];
    if (stored && models.some((model) => model.id === stored)) {
      return stored;
    }
    if (models.some((model) => model.id === provider.defaultModel)) {
      return provider.defaultModel;
    }
    return models[0]?.id || provider.defaultModel;
  }

  function selectedCustomModelForProvider(provider: ProviderSummary): string {
    return appState.value?.preferences?.customModelsByProvider?.[provider.id] || "";
  }

  function syncModelSelection(): void {
    const provider = selectedProvider.value;
    if (!provider) {
      selectedModelId.value = "";
      customModelInput.value = "";
      return;
    }
    selectedModelId.value = selectedModelForProvider(provider);
    customModelInput.value = selectedCustomModelForProvider(provider);
  }

  function updateAppState(nextState: AppState): void {
    const previousProviderId = selectedProviderId.value;
    // 仅首次加载恢复预算；保存密钥/偏好或刷新模型列表不能覆盖正在编辑的值。
    if (!appState.value) {
      translationMaxTokensInput.value = nextState.preferences?.translationMaxTokens ?? "";
    }
    appState.value = nextState;
    selectedProviderId.value =
      (providers.value.some((provider) => provider.id === previousProviderId) && previousProviderId) ||
      nextState.preferences?.lastProviderId ||
      nextState.preferredProviderId ||
      "deepseek";
    if (!providerById(selectedOnboardingProviderId.value)) {
      selectedOnboardingProviderId.value = "deepseek";
    }
    syncModelSelection();
    void appRuntime.runPromise(loadDynamicModelsProgram(selectedProvider.value, providerPorts));
  }

  const providerPorts: ProviderPorts = {
    appState,
    selectedProviderId,
    selectedModelId,
    customModelInput,
    translationMaxTokensInput,
    getSelectedProvider: () => selectedProvider.value,
    hasDynamicModels: (providerId) => Boolean(dynamicModelsByProvider[providerId]),
    storeDynamicModels: (providerId, models) => {
      dynamicModelsByProvider[providerId] = models;
    },
    syncModelSelection,
    updateAppState,
    clearApiKeyDraft: (providerId) => {
      apiKeyDrafts[providerId] = "";
    },
    clearOnboardingKey: () => {
      onboardingApiKey.value = "";
    },
    appendLog: options.appendLog,
  };

  async function persistProviderPreference(): Promise<void> {
    if (outputBudgetError.value) {
      return;
    }
    await appRuntime.runPromise(persistProviderPreferenceProgram(providerPorts));
  }

  async function onProviderChanged(): Promise<void> {
    syncModelSelection();
    void appRuntime.runPromise(loadDynamicModelsProgram(selectedProvider.value, providerPorts));
    await persistProviderPreference();
  }

  async function onModelChanged(): Promise<void> {
    await persistProviderPreference();
  }

  function modelSelection(): ModelSelection {
    const provider = selectedProvider.value;
    if (!provider) {
      throw new Error("未选择翻译服务");
    }
    return {
      mode: "service",
      providerId: provider.id,
      modelId: selectedModelId.value,
      customModelId: cleanString(customModelInput.value),
      translationMaxTokens: parseTranslationMaxTokens(translationMaxTokensInput.value),
    };
  }

  async function saveSettingsKey(providerId: string): Promise<void> {
    await appRuntime.runPromise(saveSettingsKeyProgram(providerId, apiKeyDrafts[providerId] || "", providerPorts));
  }

  async function clearSettingsKey(providerId: string): Promise<void> {
    await appRuntime.runPromise(clearSettingsKeyProgram(providerId, providerPorts));
  }

  function selectOnboardingProvider(providerId: string): void {
    selectedOnboardingProviderId.value = providerId;
  }

  async function saveOnboardingKey(): Promise<void> {
    await appRuntime.runPromise(saveOnboardingKeyProgram(selectedOnboardingProviderId.value, onboardingApiKey.value, providerPorts));
  }

  function openSettingsFromOnboarding(): void {
    onboardingDismissed.value = true;
    options.setActiveTab("settings");
  }

  function dismissOnboarding(): void {
    onboardingDismissed.value = true;
  }

  function configureProvider(): void {
    options.setActiveTab("settings");
    void nextTick(() => {
      document.querySelector(`[data-provider-card="${selectedProviderId.value}"]`)?.scrollIntoView({ block: "center" });
    });
  }

  return {
    apiKeyDrafts,
    appState,
    clearSettingsKey,
    configureProvider,
    configureProviderText,
    customModelInput,
    translationMaxTokensInput,
    outputBudgetError,
    onOutputBudgetInput,
    dismissOnboarding,
    ffmpegAvailable,
    modelSelection,
    onboardingApiKey,
    onboardingKeyLabel,
    onModelChanged,
    onProviderChanged,
    openSettingsFromOnboarding,
    persistProviderPreference,
    providerCredentialStatus,
    providerModels,
    providers,
    saveOnboardingKey,
    saveSettingsKey,
    selectedModelId,
    selectedOnboardingProviderId,
    selectedProvider,
    selectedProviderId,
    selectOnboardingProvider,
    showConfigureProvider,
    showCustomModelInput,
    showOnboarding,
    statusPillClass,
    syncModelSelection,
    updateAppState,
    visibleOnboardingProviders,
    runtimeInfo,
  };
}
