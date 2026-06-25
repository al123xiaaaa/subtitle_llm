import { computed, nextTick, reactive, ref } from "vue";
import type { AppState, ModelSelection, ProviderSummary } from "../../../types";
import type { TaskTab } from "./controllerTypes";
import { cleanString, statusPillClass } from "./controllerUtils";

interface ProviderSettingsOptions {
  appendLog: (text: string, kind?: "stdout" | "stderr") => void;
  getUseYamlConfig: () => boolean;
  setActiveTab: (tab: TaskTab) => void;
}

export function useProviderSettings(api: Window["subtitleLLM"], options: ProviderSettingsOptions) {
  const appState = ref<AppState | null>(null);
  const selectedProviderId = ref("deepseek");
  const selectedModelId = ref("");
  const customModelInput = ref("");
  const selectedOnboardingProviderId = ref("deepseek");
  const onboardingApiKey = ref("");
  const onboardingDismissed = ref(false);
  const apiKeyDrafts = reactive<Record<string, string>>({});

  const providers = computed(() => appState.value?.providers || []);
  const selectedProvider = computed(() => providerById(selectedProviderId.value));
  const providerModels = computed(() => selectedProvider.value?.models || []);
  const showCustomModelInput = computed(() => selectedModelId.value === "__custom__");
  const ffmpegAvailable = computed(() => Boolean(appState.value?.ffmpeg?.available));
  const runtimeInfo = computed(() => {
    if (!appState.value) {
      return "加载中";
    }
    return appState.value.hasMainPy ? appState.value.pythonExecutable : "未找到 main.py";
  });
  const visibleOnboardingProviders = computed(() =>
    providers.value.filter((provider) => ["deepseek", "gemini", "openai"].includes(provider.id)),
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
  const configureProviderText = computed(() =>
    selectedProvider.value ? `配置 ${selectedProvider.value.name} API Key` : "配置 API Key",
  );
  const showConfigureProvider = computed(
    () => !options.getUseYamlConfig() && Boolean(selectedProvider.value) && !selectedProvider.value?.credential.available,
  );

  function providerById(providerId: string): ProviderSummary | null {
    return providers.value.find((provider) => provider.id === providerId) || providers.value[0] || null;
  }

  function selectedModelForProvider(provider: ProviderSummary): string {
    const stored = appState.value?.preferences?.modelsByProvider?.[provider.id];
    if (stored && provider.models.some((model) => model.id === stored)) {
      return stored;
    }
    return provider.defaultModel;
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
  }

  async function persistProviderPreference(): Promise<void> {
    const provider = selectedProvider.value;
    if (!provider || !appState.value) {
      return;
    }

    const preferences = {
      ...appState.value.preferences,
      lastProviderId: provider.id,
      modelsByProvider: {
        ...appState.value.preferences?.modelsByProvider,
        [provider.id]: selectedModelId.value,
      },
      customModelsByProvider: {
        ...appState.value.preferences?.customModelsByProvider,
        [provider.id]: cleanString(customModelInput.value),
      },
    };
    updateAppState(await api.savePreferences(preferences));
  }

  async function onProviderChanged(): Promise<void> {
    syncModelSelection();
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
    };
  }

  async function saveSettingsKey(providerId: string): Promise<void> {
    try {
      updateAppState(await api.saveApiKey(providerId, apiKeyDrafts[providerId] || ""));
      apiKeyDrafts[providerId] = "";
    } catch (error) {
      options.appendLog(`${error instanceof Error ? error.message : String(error)}\n`, "stderr");
    }
  }

  async function clearSettingsKey(providerId: string): Promise<void> {
    try {
      updateAppState(await api.clearApiKey(providerId));
    } catch (error) {
      options.appendLog(`${error instanceof Error ? error.message : String(error)}\n`, "stderr");
    }
  }

  function selectOnboardingProvider(providerId: string): void {
    selectedOnboardingProviderId.value = providerId;
  }

  async function saveOnboardingKey(): Promise<void> {
    try {
      updateAppState(await api.saveApiKey(selectedOnboardingProviderId.value, onboardingApiKey.value));
      selectedProviderId.value = selectedOnboardingProviderId.value;
      onboardingApiKey.value = "";
      syncModelSelection();
    } catch (error) {
      options.appendLog(`${error instanceof Error ? error.message : String(error)}\n`, "stderr");
    }
  }

  function openSettingsFromOnboarding(): void {
    onboardingDismissed.value = true;
    options.setActiveTab("settings");
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
