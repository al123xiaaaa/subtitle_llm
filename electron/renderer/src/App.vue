<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import type { AppState, CommandName, JobEvent, ModelSelection, ProviderSummary } from "../../types";

const RESULT_EVENT_PREFIX = "SUBTITLE_LLM_RESULT ";

type TaskTab = "translate" | "download" | "transcribe" | "settings";
type ResultTarget = "subtitle" | "video";

const api = window.subtitleLLM;

const taskTabs: Array<{ id: TaskTab; label: string }> = [
  { id: "translate", label: "翻译字幕" },
  { id: "download", label: "下载字幕" },
  { id: "transcribe", label: "音频转写" },
  { id: "settings", label: "设置" },
];

const appState = ref<AppState | null>(null);
const activeTab = ref<TaskTab>("translate");
const activeJobId = ref("");
const activeCommand = ref<CommandName | "">("");
const isBusy = ref(false);
const runStatus = ref("空闲");
const logText = ref("");
const logBody = ref<HTMLElement | null>(null);

const lastOutputPath = ref("");
const lastSubtitlePath = ref("");
const lastEmbeddedVideoPath = ref("");
const lastSourceVideoPath = ref("");

const selectedProviderId = ref("deepseek");
const selectedModelId = ref("");
const customModelInput = ref("");
const selectedOnboardingProviderId = ref("deepseek");
const onboardingApiKey = ref("");
const onboardingDismissed = ref(false);
const embedPreferenceTouched = ref(false);
const apiKeyDrafts = reactive<Record<string, string>>({});

const translateForm = reactive({
  input: "",
  targetLanguage: "Chinese",
  sourceLanguage: "en",
  outputFormat: "",
  reviewMode: "auto",
  output: "",
  config: "",
  useYamlConfig: false,
  resume: false,
  embedMkv: false,
  video: "",
});

const muxForm = reactive({
  subtitle: "",
  video: "",
  targetLanguage: "Chinese",
});

const downloadForm = reactive({
  url: "",
  sourceLanguage: "en",
  outputDir: "data/input",
});

const transcribeForm = reactive({
  audio: "",
  language: "English",
  output: "",
  config: "",
});

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
  if (translateForm.useYamlConfig) {
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
  () => !translateForm.useYamlConfig && Boolean(selectedProvider.value) && !selectedProvider.value?.credential.available,
);
const canStartTranslate = computed(
  () => !isBusy.value && Boolean(selectedProvider.value) && (translateForm.useYamlConfig || Boolean(selectedProvider.value?.credential.available)),
);
const mkvCapabilityText = computed(() => {
  if (!appState.value) {
    return "检测 FFmpeg 中";
  }
  return ffmpegAvailable.value ? "FFmpeg 可用" : appState.value.ffmpeg?.error || "未找到 FFmpeg";
});
const mkvCapabilityClass = computed(() => (ffmpegAvailable.value ? "status-pill is-saved" : "status-pill is-missing"));
const canStartMux = computed(
  () => !isBusy.value && ffmpegAvailable.value && Boolean(cleanString(muxForm.subtitle)) && Boolean(cleanString(muxForm.video)),
);
const hasSubtitleResult = computed(() => Boolean(lastSubtitlePath.value));
const hasVideoResult = computed(() => Boolean(lastEmbeddedVideoPath.value));
const hasAnyResult = computed(() => hasSubtitleResult.value || hasVideoResult.value);

watch(
  () => translateForm.targetLanguage,
  (targetLanguage) => {
    muxForm.targetLanguage = cleanString(targetLanguage) || "Chinese";
  },
);

watch(
  () => translateForm.video,
  (video) => {
    if (cleanString(video)) {
      muxForm.video = cleanString(video);
    }
  },
);

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

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

function statusPillClass(status: { available: boolean; source: string }): string {
  return status.available ? `status-pill is-${status.source}` : "status-pill is-missing";
}

function setActiveTab(tab: TaskTab): void {
  if (!isBusy.value) {
    activeTab.value = tab;
  }
}

function setBusy(nextBusy: boolean): void {
  isBusy.value = nextBusy;
  if (!nextBusy) {
    activeJobId.value = "";
  }
}

function appendLog(text: string, kind: "stdout" | "stderr" = "stdout"): void {
  const prefix = kind === "stderr" ? "[stderr] " : "";
  logText.value += `${prefix}${text}`;
  void scrollLogToEnd();
}

async function scrollLogToEnd(): Promise<void> {
  await nextTick();
  if (logBody.value) {
    logBody.value.scrollTop = logBody.value.scrollHeight;
  }
}

function setStatus(text: string): void {
  runStatus.value = text;
}

function setOutputPath(filePath: unknown): void {
  const cleaned = cleanString(filePath);
  if (!cleaned) {
    return;
  }
  lastOutputPath.value = cleaned;
}

function setSubtitlePath(filePath: unknown): void {
  const cleaned = cleanString(filePath);
  if (!cleaned) {
    return;
  }
  lastSubtitlePath.value = cleaned;
  setOutputPath(cleaned);
  muxForm.subtitle = cleaned;
}

function setSourceVideoPath(filePath: unknown): void {
  const cleaned = cleanString(filePath);
  if (!cleaned) {
    return;
  }
  lastSourceVideoPath.value = cleaned;
  muxForm.video = cleaned;
}

function setEmbeddedVideoPath(filePath: unknown): void {
  const cleaned = cleanString(filePath);
  if (!cleaned) {
    return;
  }
  lastEmbeddedVideoPath.value = cleaned;
  setOutputPath(cleaned);
}

function parseKnownOutput(text: string): void {
  for (const line of text.split(/\r?\n/)) {
    if (parseResultEvent(line)) {
      continue;
    }
    const outputMatch = line.match(/^输出文件：(.+)$/);
    if (outputMatch) {
      setSubtitlePath(outputMatch[1]);
    }
    const sourceVideoMatch = line.match(/^源视频：(.+)$/);
    if (sourceVideoMatch) {
      setSourceVideoPath(sourceVideoMatch[1]);
    }
    const embeddedVideoMatch = line.match(/^输出视频：(.+)$/);
    if (embeddedVideoMatch) {
      setEmbeddedVideoPath(embeddedVideoMatch[1]);
    }
  }
}

function parseResultEvent(line: string): boolean {
  if (!line.startsWith(RESULT_EVENT_PREFIX)) {
    return false;
  }

  try {
    const event = JSON.parse(line.slice(RESULT_EVENT_PREFIX.length)) as Record<string, unknown>;
    setSubtitlePath(event.output_file);
    setSourceVideoPath(event.source_video_file);
    setEmbeddedVideoPath(event.embedded_video_file);
    setEmbeddedVideoPath(event.output_video_file);
    return true;
  } catch (error) {
    appendLog(`结果事件解析失败：${error instanceof Error ? error.message : String(error)}\n`, "stderr");
    return true;
  }
}

function looksLikeUrl(rawValue: string): boolean {
  try {
    const parsed = new URL(rawValue);
    return ["http:", "https:"].includes(parsed.protocol);
  } catch {
    return false;
  }
}

function syncEmbedDefault(): void {
  if (embedPreferenceTouched.value || !ffmpegAvailable.value) {
    return;
  }
  translateForm.embedMkv = looksLikeUrl(cleanString(translateForm.input));
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
  syncEmbedDefault();
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
      ...(appState.value.preferences?.modelsByProvider || {}),
      [provider.id]: selectedModelId.value,
    },
    customModelsByProvider: {
      ...(appState.value.preferences?.customModelsByProvider || {}),
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

async function startJob(command: CommandName, options: Record<string, unknown>, selection: ModelSelection | null = null): Promise<void> {
  try {
    setBusy(true);
    setStatus("启动中");
    appendLog(`\n$ subtitle-llm ${command}\n`);
    const response = await api.startJob({
      command,
      options,
      modelSelection: selection,
    });
    activeJobId.value = response.jobId;
    activeCommand.value = command;
  } catch (error) {
    setBusy(false);
    setStatus("启动失败");
    appendLog(`${error instanceof Error ? error.message : String(error)}\n`, "stderr");
  }
}

async function saveSettingsKey(providerId: string): Promise<void> {
  try {
    updateAppState(await api.saveApiKey(providerId, apiKeyDrafts[providerId] || ""));
    apiKeyDrafts[providerId] = "";
  } catch (error) {
    appendLog(`${error instanceof Error ? error.message : String(error)}\n`, "stderr");
  }
}

async function clearSettingsKey(providerId: string): Promise<void> {
  try {
    updateAppState(await api.clearApiKey(providerId));
  } catch (error) {
    appendLog(`${error instanceof Error ? error.message : String(error)}\n`, "stderr");
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
    appendLog(`${error instanceof Error ? error.message : String(error)}\n`, "stderr");
  }
}

function openSettingsFromOnboarding(): void {
  onboardingDismissed.value = true;
  activeTab.value = "settings";
}

function configureProvider(): void {
  activeTab.value = "settings";
  void nextTick(() => {
    document.querySelector(`[data-provider-card="${selectedProviderId.value}"]`)?.scrollIntoView({ block: "center" });
  });
}

function assignIfSelected(assign: (filePath: string) => void, filePath: string | null): void {
  const cleaned = cleanString(filePath);
  if (cleaned) {
    assign(cleaned);
  }
}

async function chooseInput(): Promise<void> {
  assignIfSelected((filePath) => {
    translateForm.input = filePath;
  }, await api.selectInput());
  syncEmbedDefault();
}

async function chooseTranslateVideo(): Promise<void> {
  assignIfSelected((filePath) => {
    translateForm.video = filePath;
    translateForm.embedMkv = true;
    embedPreferenceTouched.value = true;
    muxForm.video = filePath;
  }, await api.selectVideo());
}

async function chooseTranslateConfig(): Promise<void> {
  assignIfSelected((filePath) => {
    translateForm.config = filePath;
  }, await api.selectConfig());
}

async function chooseTranscribeConfig(): Promise<void> {
  assignIfSelected((filePath) => {
    transcribeForm.config = filePath;
  }, await api.selectConfig());
}

async function chooseDownloadDir(): Promise<void> {
  assignIfSelected((filePath) => {
    downloadForm.outputDir = filePath;
  }, await api.selectDirectory());
}

async function chooseMuxSubtitle(): Promise<void> {
  assignIfSelected((filePath) => {
    muxForm.subtitle = filePath;
  }, await api.selectSubtitle());
}

async function chooseMuxVideo(): Promise<void> {
  assignIfSelected((filePath) => {
    muxForm.video = filePath;
  }, await api.selectVideo());
}

async function chooseAudio(): Promise<void> {
  const audio = await api.selectAudio();
  assignIfSelected((filePath) => {
    transcribeForm.audio = filePath;
    if (!cleanString(transcribeForm.output)) {
      transcribeForm.output = filePath.replace(/\.[^.]+$/, ".srt");
    }
  }, audio);
}

async function chooseOutput(): Promise<void> {
  const defaultName = cleanString(translateForm.input).replace(/\.[^.]+$/, ".zh.srt") || "output.zh.srt";
  assignIfSelected((filePath) => {
    translateForm.output = filePath;
  }, await api.saveSrt(defaultName));
}

async function chooseTranscribeOutput(): Promise<void> {
  const defaultName = cleanString(transcribeForm.audio).replace(/\.[^.]+$/, ".srt") || "transcript.srt";
  assignIfSelected((filePath) => {
    transcribeForm.output = filePath;
  }, await api.saveSrt(defaultName));
}

function onTranslateInput(): void {
  syncEmbedDefault();
}

function onEmbedMkvChanged(): void {
  embedPreferenceTouched.value = true;
}

async function submitTranslate(): Promise<void> {
  const output = cleanString(translateForm.output);
  if (output) {
    setSubtitlePath(output);
  }
  await persistProviderPreference();
  const input = cleanString(translateForm.input);
  const video = cleanString(translateForm.video);
  const embedVideo = translateForm.embedMkv && ffmpegAvailable.value && (looksLikeUrl(input) || Boolean(video));
  await startJob(
    "translate",
    {
      input,
      targetLanguage: cleanString(translateForm.targetLanguage),
      sourceLanguage: cleanString(translateForm.sourceLanguage),
      output,
      config: translateForm.useYamlConfig ? cleanString(translateForm.config) : "",
      outputFormat: cleanString(translateForm.outputFormat),
      reviewMode: cleanString(translateForm.reviewMode),
      resume: translateForm.resume,
      embedVideo,
      video,
    },
    translateForm.useYamlConfig ? null : modelSelection(),
  );
}

async function submitMux(): Promise<void> {
  await startJob("mux", {
    subtitle: cleanString(muxForm.subtitle),
    video: cleanString(muxForm.video),
    targetLanguage: cleanString(muxForm.targetLanguage) || cleanString(translateForm.targetLanguage) || "Chinese",
  });
}

async function submitDownload(): Promise<void> {
  await startJob("download", {
    url: cleanString(downloadForm.url),
    outputDir: cleanString(downloadForm.outputDir),
    sourceLanguage: cleanString(downloadForm.sourceLanguage),
  });
}

async function submitTranscribe(): Promise<void> {
  const output = cleanString(transcribeForm.output);
  setOutputPath(output);
  await startJob("transcribe", {
    audio: cleanString(transcribeForm.audio),
    output,
    language: cleanString(transcribeForm.language),
    config: cleanString(transcribeForm.config),
  });
}

async function cancelJob(): Promise<void> {
  if (!activeJobId.value) {
    return;
  }
  const result = await api.cancelJob(activeJobId.value);
  if (result.ok) {
    setStatus("正在取消");
    appendLog("正在取消任务...\n");
  }
}

function clearLog(): void {
  logText.value = "";
}

async function openResult(target: ResultTarget): Promise<void> {
  const filePath = target === "video" ? lastEmbeddedVideoPath.value : lastSubtitlePath.value;
  if (filePath) {
    await api.openPath(filePath);
  }
}

async function showResult(target: ResultTarget): Promise<void> {
  const filePath = target === "video" ? lastEmbeddedVideoPath.value : lastSubtitlePath.value;
  if (filePath) {
    await api.showInFolder(filePath);
  }
}

async function openOutput(): Promise<void> {
  if (lastOutputPath.value) {
    await api.openPath(lastOutputPath.value);
  }
}

async function showOutput(): Promise<void> {
  if (lastOutputPath.value) {
    await api.showInFolder(lastOutputPath.value);
  }
}

function handleJobEvent(event: JobEvent): void {
  if (event.type === "started") {
    setStatus("运行中");
    appendLog(`Python: ${event.pythonExecutable}\n工作目录: ${event.cwd}\n`);
    if (event.generatedConfigPath) {
      appendLog(`模型配置: ${event.generatedConfigPath}\n`);
    }
  } else if (event.type === "stdout") {
    appendLog(event.text);
    parseKnownOutput(event.text);
  } else if (event.type === "stderr") {
    appendLog(event.text, "stderr");
  } else if (event.type === "error") {
    setBusy(false);
    setStatus("失败");
    appendLog(`${event.message}\n`, "stderr");
  } else if (event.type === "finished") {
    setBusy(false);
    setStatus(event.code === 0 ? "完成" : `退出码 ${event.code}`);
    appendLog(`\n任务结束：code=${event.code} signal=${event.signal || "none"}\n`, event.code === 0 ? "stdout" : "stderr");
  }
}

let unsubscribeJobEvents: (() => void) | null = null;

onMounted(async () => {
  unsubscribeJobEvents = api.onJobEvent(handleJobEvent);
  try {
    updateAppState(await api.getState());
  } catch (error) {
    setStatus("加载失败");
    appendLog(`${error instanceof Error ? error.message : String(error)}\n`, "stderr");
  }
});

onUnmounted(() => {
  unsubscribeJobEvents?.();
});
</script>

<template>
  <div id="onboarding" :class="['onboarding', { 'is-hidden': !showOnboarding }]" aria-modal="true" role="dialog">
    <section class="onboarding-panel">
      <p class="eyebrow">首次配置</p>
      <h1>先连接一个翻译服务</h1>
      <p>保存 API Key 后就可以开始翻译字幕。也可以使用系统环境变量，App 会自动识别。</p>
      <div id="onboardingProviders" class="provider-cards">
        <button
          v-for="provider in visibleOnboardingProviders"
          :key="provider.id"
          :class="['provider-card', { 'is-selected': provider.id === selectedOnboardingProviderId }]"
          type="button"
          :data-onboarding-provider="provider.id"
          :disabled="isBusy"
          @click="selectOnboardingProvider(provider.id)"
        >
          <strong>{{ provider.name }}</strong>
          <span>{{ provider.id === "deepseek" ? "推荐" : provider.credential.envKey }}</span>
        </button>
      </div>
      <label>
        <span id="onboardingKeyLabel">{{ onboardingKeyLabel }}</span>
        <input id="onboardingApiKey" v-model="onboardingApiKey" type="password" autocomplete="off" :disabled="isBusy" />
      </label>
      <div class="actions">
        <button id="onboardingSave" class="primary-button" type="button" :disabled="isBusy" @click="saveOnboardingKey">
          保存并开始
        </button>
        <button
          id="onboardingSettings"
          class="secondary-button"
          type="button"
          :disabled="isBusy"
          @click="openSettingsFromOnboarding"
        >
          打开设置
        </button>
      </div>
    </section>
  </div>

  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">SL</div>
        <div>
          <h1>Subtitle LLM</h1>
          <p id="runtimeInfo">{{ runtimeInfo }}</p>
        </div>
      </div>
      <nav class="tabs" aria-label="任务类型">
        <button
          v-for="tab in taskTabs"
          :key="tab.id"
          :class="['tab', { 'is-active': activeTab === tab.id }]"
          type="button"
          :data-tab="tab.id"
          :disabled="isBusy"
          @click="setActiveTab(tab.id)"
        >
          {{ tab.label }}
        </button>
      </nav>
      <section class="status-panel" aria-labelledby="statusTitle">
        <h2 id="statusTitle">服务状态</h2>
        <div id="sidebarProviderStatus" class="provider-status-list">
          <div v-for="provider in providers" :key="provider.id" class="provider-status-item">
            <span>{{ provider.name }}</span>
            <span :class="statusPillClass(provider.credential)">{{ provider.credential.label }}</span>
          </div>
        </div>
      </section>
    </aside>

    <main class="workspace">
      <section
        :class="['task-panel', { 'is-active': activeTab === 'translate' }]"
        data-panel="translate"
        aria-labelledby="translateTitle"
      >
        <div class="panel-heading">
          <div>
            <h2 id="translateTitle">翻译字幕</h2>
            <p>SRT、转写 JSON 或视频 URL</p>
          </div>
          <button id="chooseInput" class="secondary-button" type="button" :disabled="isBusy" @click="chooseInput">选择文件</button>
        </div>
        <form id="translateForm" class="form-grid" @submit.prevent="submitTranslate">
          <label>
            <span>翻译服务</span>
            <select id="providerSelect" v-model="selectedProviderId" :disabled="isBusy" @change="onProviderChanged">
              <option v-for="provider in providers" :key="provider.id" :value="provider.id">{{ provider.name }}</option>
            </select>
          </label>
          <label>
            <span>模型</span>
            <select id="modelSelect" v-model="selectedModelId" :disabled="isBusy" @change="onModelChanged">
              <option v-for="model in providerModels" :key="model.id" :value="model.id">
                {{ model.description ? `${model.label} - ${model.description}` : model.label }}
              </option>
            </select>
          </label>
          <label id="customModelRow" :class="['span-2', { 'is-hidden': !showCustomModelInput }]">
            <span>自定义模型 ID</span>
            <input id="customModelInput" v-model="customModelInput" type="text" autocomplete="off" :disabled="isBusy" @change="persistProviderPreference" />
          </label>
          <div class="span-2 credential-row">
            <span id="providerCredentialStatus" :class="providerCredentialStatus.className">{{ providerCredentialStatus.text }}</span>
            <button
              id="configureProvider"
              :class="['secondary-button', { 'is-hidden': !showConfigureProvider }]"
              type="button"
              :disabled="isBusy"
              @click="configureProvider"
            >
              {{ configureProviderText }}
            </button>
          </div>
          <label class="span-2">
            <span>输入</span>
            <input
              id="translateInput"
              v-model="translateForm.input"
              name="input"
              type="text"
              placeholder="input.srt / input.json / https://..."
              required
              :disabled="isBusy"
              @input="onTranslateInput"
            />
          </label>
          <label>
            <span>目标语言</span>
            <input id="targetLanguage" v-model="translateForm.targetLanguage" name="targetLanguage" type="text" required :disabled="isBusy" />
          </label>
          <label>
            <span>源语言</span>
            <input id="sourceLanguage" v-model="translateForm.sourceLanguage" name="sourceLanguage" type="text" :disabled="isBusy" />
          </label>
          <label>
            <span>输出格式</span>
            <select id="outputFormat" v-model="translateForm.outputFormat" name="outputFormat" :disabled="isBusy">
              <option value="">使用配置默认值</option>
              <option value="source-first">原文在前</option>
              <option value="target-first">译文在前</option>
              <option value="bilingual">双语</option>
              <option value="target-only">仅译文</option>
              <option value="source-only">仅原文</option>
            </select>
          </label>
          <label>
            <span>复核方式</span>
            <select id="reviewMode" v-model="translateForm.reviewMode" name="reviewMode" :disabled="isBusy">
              <option value="auto">自动修复</option>
              <option value="config">使用配置</option>
              <option value="tui">TUI 复核</option>
            </select>
          </label>
          <label class="span-2">
            <span>输出文件</span>
            <div class="inline-control">
              <input
                id="translateOutput"
                v-model="translateForm.output"
                name="output"
                type="text"
                placeholder="默认写入 data/output"
                :disabled="isBusy"
              />
              <button id="chooseOutput" class="secondary-button" type="button" :disabled="isBusy" @click="chooseOutput">选择</button>
            </div>
          </label>
          <section class="mkv-panel span-2" aria-labelledby="embedMkvTitle">
            <div class="mkv-panel-heading">
              <label class="check-row">
                <input
                  id="embedMkv"
                  v-model="translateForm.embedMkv"
                  type="checkbox"
                  :disabled="isBusy || !ffmpegAvailable"
                  @change="onEmbedMkvChanged"
                />
                <span id="embedMkvTitle">翻译完成后生成带字幕 MKV</span>
              </label>
              <span id="mkvCapabilityStatus" :class="mkvCapabilityClass">{{ mkvCapabilityText }}</span>
            </div>
            <label>
              <span>视频文件</span>
              <div class="inline-control">
                <input
                  id="translateVideo"
                  v-model="translateForm.video"
                  type="text"
                  placeholder="视频 URL 会自动复用下载到的视频；本地字幕可手动选择"
                  :disabled="isBusy || !ffmpegAvailable"
                />
                <button
                  id="chooseTranslateVideo"
                  class="secondary-button"
                  type="button"
                  :disabled="isBusy || !ffmpegAvailable"
                  @click="chooseTranslateVideo"
                >
                  选择
                </button>
              </div>
            </label>
          </section>
          <details class="advanced-panel span-2">
            <summary>高级配置</summary>
            <label class="check-row">
              <input id="useYamlConfig" v-model="translateForm.useYamlConfig" type="checkbox" :disabled="isBusy" />
              <span>使用 YAML 配置文件覆盖服务商和模型选择</span>
            </label>
            <label>
              <span>配置文件</span>
              <div class="inline-control">
                <input
                  id="translateConfig"
                  v-model="translateForm.config"
                  name="config"
                  type="text"
                  placeholder="默认配置"
                  :disabled="!translateForm.useYamlConfig || isBusy"
                />
                <button
                  id="chooseTranslateConfig"
                  class="secondary-button"
                  type="button"
                  :disabled="!translateForm.useYamlConfig || isBusy"
                  @click="chooseTranslateConfig"
                >
                  选择
                </button>
              </div>
            </label>
          </details>
          <label class="check-row">
            <input id="resumeTranslate" v-model="translateForm.resume" name="resume" type="checkbox" :disabled="isBusy" />
            <span>从断点继续</span>
          </label>
          <div class="actions span-2">
            <button id="startTranslate" class="primary-button" type="submit" :disabled="!canStartTranslate">开始翻译</button>
          </div>
        </form>
        <form id="muxForm" class="form-grid mux-form" @submit.prevent="submitMux">
          <div class="span-2 form-section-heading">
            <h3>已有字幕生成 MKV</h3>
            <p>翻译后想补生成视频时，在这里选择字幕和视频即可。</p>
          </div>
          <label>
            <span>已翻译字幕</span>
            <div class="inline-control">
              <input id="muxSubtitle" v-model="muxForm.subtitle" type="text" placeholder="output.zh.srt" :disabled="isBusy || !ffmpegAvailable" />
              <button id="chooseMuxSubtitle" class="secondary-button" type="button" :disabled="isBusy || !ffmpegAvailable" @click="chooseMuxSubtitle">
                选择
              </button>
            </div>
          </label>
          <label>
            <span>视频文件</span>
            <div class="inline-control">
              <input id="muxVideo" v-model="muxForm.video" type="text" placeholder="video.mp4 / video.mkv" :disabled="isBusy || !ffmpegAvailable" />
              <button id="chooseMuxVideo" class="secondary-button" type="button" :disabled="isBusy || !ffmpegAvailable" @click="chooseMuxVideo">
                选择
              </button>
            </div>
          </label>
          <label>
            <span>字幕语言</span>
            <input id="muxTargetLanguage" v-model="muxForm.targetLanguage" type="text" :disabled="isBusy || !ffmpegAvailable" />
          </label>
          <div class="actions">
            <button id="startMux" class="primary-button" type="submit" :disabled="!canStartMux">生成 MKV</button>
          </div>
        </form>
      </section>

      <section
        :class="['task-panel', { 'is-active': activeTab === 'download' }]"
        data-panel="download"
        aria-labelledby="downloadTitle"
      >
        <div class="panel-heading">
          <div>
            <h2 id="downloadTitle">下载字幕</h2>
            <p>视频地址与字幕语言</p>
          </div>
        </div>
        <form id="downloadForm" class="form-grid" @submit.prevent="submitDownload">
          <label class="span-2">
            <span>视频 URL</span>
            <input id="downloadUrl" v-model="downloadForm.url" name="url" type="url" placeholder="https://..." required :disabled="isBusy" />
          </label>
          <label>
            <span>源语言</span>
            <input id="downloadSourceLanguage" v-model="downloadForm.sourceLanguage" name="sourceLanguage" type="text" :disabled="isBusy" />
          </label>
          <label>
            <span>输出文件夹</span>
            <div class="inline-control">
              <input id="downloadOutputDir" v-model="downloadForm.outputDir" name="outputDir" type="text" :disabled="isBusy" />
              <button id="chooseDownloadDir" class="secondary-button" type="button" :disabled="isBusy" @click="chooseDownloadDir">选择</button>
            </div>
          </label>
          <div class="actions span-2">
            <button class="primary-button" type="submit" :disabled="isBusy">开始下载</button>
          </div>
        </form>
      </section>

      <section
        :class="['task-panel', { 'is-active': activeTab === 'transcribe' }]"
        data-panel="transcribe"
        aria-labelledby="transcribeTitle"
      >
        <div class="panel-heading">
          <div>
            <h2 id="transcribeTitle">音频转写</h2>
            <p>生成 SRT 后可继续翻译</p>
          </div>
          <button id="chooseAudio" class="secondary-button" type="button" :disabled="isBusy" @click="chooseAudio">选择音频</button>
        </div>
        <form id="transcribeForm" class="form-grid" @submit.prevent="submitTranscribe">
          <label class="span-2">
            <span>音频文件</span>
            <input id="audioInput" v-model="transcribeForm.audio" name="audio" type="text" required :disabled="isBusy" />
          </label>
          <label>
            <span>语言</span>
            <input id="transcribeLanguage" v-model="transcribeForm.language" name="language" type="text" :disabled="isBusy" />
          </label>
          <label>
            <span>输出 SRT</span>
            <div class="inline-control">
              <input id="transcribeOutput" v-model="transcribeForm.output" name="output" type="text" required :disabled="isBusy" />
              <button id="chooseTranscribeOutput" class="secondary-button" type="button" :disabled="isBusy" @click="chooseTranscribeOutput">
                选择
              </button>
            </div>
          </label>
          <label class="span-2">
            <span>配置文件</span>
            <div class="inline-control">
              <input id="transcribeConfig" v-model="transcribeForm.config" name="config" type="text" placeholder="默认配置" :disabled="isBusy" />
              <button id="chooseTranscribeConfig" class="secondary-button" type="button" :disabled="isBusy" @click="chooseTranscribeConfig">
                选择
              </button>
            </div>
          </label>
          <div class="actions span-2">
            <button class="primary-button" type="submit" :disabled="isBusy">开始转写</button>
          </div>
        </form>
      </section>

      <section
        :class="['task-panel', { 'is-active': activeTab === 'settings' }]"
        data-panel="settings"
        aria-labelledby="settingsTitle"
      >
        <div class="panel-heading">
          <div>
            <h2 id="settingsTitle">设置</h2>
            <p>管理翻译服务 API Key</p>
          </div>
        </div>
        <div id="settingsCards" class="settings-grid">
          <article v-for="provider in providers" :key="provider.id" class="settings-card" :data-provider-card="provider.id">
            <div class="settings-card-heading">
              <div>
                <h3>{{ provider.name }}</h3>
                <p>{{ provider.credential.envKey }}</p>
              </div>
              <span :class="statusPillClass(provider.credential)">{{ provider.credential.label }}</span>
            </div>
            <label>
              <span>{{ provider.name }} API Key</span>
              <input
                v-model="apiKeyDrafts[provider.id]"
                :data-api-key-input="provider.id"
                type="password"
                autocomplete="off"
                placeholder="粘贴新的 API Key"
                :disabled="isBusy"
              />
            </label>
            <div class="card-actions">
              <button
                class="primary-button"
                type="button"
                :data-save-key="provider.id"
                :disabled="isBusy"
                @click="saveSettingsKey(provider.id)"
              >
                保存
              </button>
              <button
                class="secondary-button"
                type="button"
                :data-clear-key="provider.id"
                :disabled="isBusy"
                @click="clearSettingsKey(provider.id)"
              >
                清除
              </button>
            </div>
          </article>
        </div>
      </section>

      <section class="run-panel" aria-labelledby="runTitle">
        <div class="run-heading">
          <div>
            <h2 id="runTitle">任务日志</h2>
            <p id="runStatus">{{ runStatus }}</p>
          </div>
          <div class="run-actions">
            <button id="openOutput" class="secondary-button" type="button" :disabled="!lastOutputPath" @click="openOutput">打开输出</button>
            <button id="showOutput" class="secondary-button" type="button" :disabled="!lastOutputPath" @click="showOutput">定位文件</button>
            <button id="cancelJob" class="danger-button" type="button" :disabled="!isBusy" @click="cancelJob">取消</button>
            <button id="clearLog" class="secondary-button" type="button" @click="clearLog">清空</button>
          </div>
        </div>
        <div id="resultFiles" :class="['result-files', { 'is-hidden': !hasAnyResult }]">
          <div id="subtitleResultRow" :class="['result-file-row', { 'is-hidden': !hasSubtitleResult }]">
            <div>
              <strong>字幕文件</strong>
              <span id="subtitleResultPath">{{ lastSubtitlePath }}</span>
            </div>
            <div class="result-actions">
              <button class="secondary-button" type="button" data-open-result="subtitle" @click="openResult('subtitle')">打开</button>
              <button class="secondary-button" type="button" data-show-result="subtitle" @click="showResult('subtitle')">定位</button>
            </div>
          </div>
          <div id="videoResultRow" :class="['result-file-row', { 'is-hidden': !hasVideoResult }]">
            <div>
              <strong>带字幕 MKV</strong>
              <span id="videoResultPath">{{ lastEmbeddedVideoPath }}</span>
            </div>
            <div class="result-actions">
              <button class="secondary-button" type="button" data-open-result="video" @click="openResult('video')">打开</button>
              <button class="secondary-button" type="button" data-show-result="video" @click="showResult('video')">定位</button>
            </div>
          </div>
        </div>
        <pre id="logBody" ref="logBody" aria-live="polite">{{ logText }}</pre>
      </section>
    </main>
  </div>
</template>
