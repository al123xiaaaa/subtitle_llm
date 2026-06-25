import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { Languages, Download, AudioLines, Settings as SettingsIcon, X } from "@lucide/vue";
import type {
  AppState,
  CliResultEvent,
  CliProgressEvent,
  CommandName,
  DesktopJobRequest,
  JobEvent,
  ModelSelection,
  OutputFormat,
  ProviderSummary,
  ReviewMode,
} from "../../../types";
import { useJobProgress } from "./useJobProgress";
import { formatProgressLogLine } from "../../../lib/progressModel";

const RESULT_EVENT_PREFIX = "SUBTITLE_LLM_RESULT ";
const PROGRESS_EVENT_PREFIX = "SUBTITLE_LLM_PROGRESS ";

type TaskTab = "translate" | "download" | "transcribe" | "settings";
type ResultTarget = "subtitle" | "trace" | "video";

const taskTabs: Array<{ id: TaskTab; label: string; icon: unknown }> = [
  { id: "translate", label: "翻译字幕", icon: Languages },
  { id: "download", label: "下载字幕", icon: Download },
  { id: "transcribe", label: "音频转写", icon: AudioLines },
  { id: "settings", label: "设置", icon: SettingsIcon },
];

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function looksLikeUrl(rawValue: string): boolean {
  try {
    const parsed = new URL(rawValue);
    return ["http:", "https:"].includes(parsed.protocol);
  } catch {
    return false;
  }
}

function statusPillClass(status: { available: boolean; source: string }): string {
  return status.available ? `status-pill is-${status.source}` : "status-pill is-missing";
}

export function useAppController() {
  const api = window.subtitleLLM;
  const appState = ref<AppState | null>(null);
  const activeTab = ref<TaskTab>("translate");
  const configDrawerOpen = ref(true);
  const activeJobId = ref("");
  const activeCommand = ref<CommandName | "">("");
  const isBusy = ref(false);
  const runStatus = ref("空闲");
  const logText = ref("");
  const logBody = ref<HTMLElement | null>(null);
  let lastLogStage = "";
  const {
    chunkStatusLabel,
    chunkTooltip,
    finishJobProgress,
    progressChunkSummary,
    progressChunks,
    progressLongWaitHint,
    progressStages,
    progressState,
    progressWaitText,
    recordProgress,
    resetProgress,
    selectChunk,
    selectedProgressChunk,
  } = useJobProgress();

  const lastOutputPath = ref("");
  const lastSubtitlePath = ref("");
  const lastEmbeddedVideoPath = ref("");
  const lastLlmTraceDir = ref("");
  const lastSourceVideoPath = ref("");

  const selectedProviderId = ref("deepseek");
  const selectedModelId = ref("");
  const customModelInput = ref("");
  const selectedOnboardingProviderId = ref("deepseek");
  const onboardingApiKey = ref("");
  const onboardingDismissed = ref(false);
  const embedPreferenceTouched = ref(false);
  const apiKeyDrafts = reactive<Record<string, string>>({});

  const translateForm = reactive<{
    input: string;
    targetLanguage: string;
    sourceLanguage: string;
    outputFormat: OutputFormat | "";
    reviewMode: ReviewMode;
    output: string;
    config: string;
    useYamlConfig: boolean;
    refineTranslation: boolean;
    resume: boolean;
    embedMkv: boolean;
    video: string;
  }>({
    input: "",
    targetLanguage: "Chinese",
    sourceLanguage: "en",
    outputFormat: "",
    reviewMode: "auto",
    output: "",
    config: "",
    useYamlConfig: false,
    refineTranslation: false,
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
  const drawerSubtitle = computed(() => {
    switch (activeTab.value) {
      case "translate":
        return "SRT、转写 JSON 或视频 URL";
      case "download":
        return "视频地址与字幕语言";
      case "transcribe":
        return "生成 SRT 后可继续翻译";
      case "settings":
        return "管理翻译服务 API Key";
      default:
        return "";
    }
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
  const hasTraceResult = computed(() => Boolean(lastLlmTraceDir.value));
  const hasAnyResult = computed(() => hasSubtitleResult.value || hasVideoResult.value || hasTraceResult.value);

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

  function setActiveTab(tab: TaskTab): void {
    activeTab.value = tab;
    configDrawerOpen.value = true;
  }

  function toggleConfigDrawer(): void {
    configDrawerOpen.value = !configDrawerOpen.value;
  }

  function setBusy(nextBusy: boolean): void {
    isBusy.value = nextBusy;
    // 任务运行时收起配置抽屉，让进度主区独占；任务结束后不自动展开，留给用户决定。
    if (nextBusy) {
      configDrawerOpen.value = false;
    }
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

  function setLlmTraceDir(filePath: unknown): void {
    const cleaned = cleanString(filePath);
    if (!cleaned) {
      return;
    }
    lastLlmTraceDir.value = cleaned;
  }

  function parseStructuredEvent(line: string): boolean {
    if (parseProgressEvent(line)) {
      return true;
    }
    return parseResultEvent(line);
  }

  function parseProgressEvent(line: string): boolean {
    if (!line.startsWith(PROGRESS_EVENT_PREFIX)) {
      return false;
    }

    try {
      const event = JSON.parse(line.slice(PROGRESS_EVENT_PREFIX.length)) as CliProgressEvent;
      recordProgress(event);
      const formatted = formatProgressLogLine(event, { previousStage: lastLogStage });
      if (formatted) {
        appendLog(`${formatted.line}\n`);
        lastLogStage = formatted.stage;
      }
      return true;
    } catch (error) {
      appendLog(`进度事件解析失败：${error instanceof Error ? error.message : String(error)}\n`, "stderr");
      return true;
    }
  }

  function parseResultEvent(line: string): boolean {
    if (!line.startsWith(RESULT_EVENT_PREFIX)) {
      return false;
    }

    try {
      const event = JSON.parse(line.slice(RESULT_EVENT_PREFIX.length)) as CliResultEvent;
      setSubtitlePath(event.output_file);
      setSourceVideoPath(event.source_video_file);
      setEmbeddedVideoPath(event.embedded_video_file);
      setEmbeddedVideoPath(event.output_video_file);
      setLlmTraceDir(event.llm_trace_dir);
      return true;
    } catch (error) {
      appendLog(`结果事件解析失败：${error instanceof Error ? error.message : String(error)}\n`, "stderr");
      return true;
    }
  }

  function consumeStructuredStdout(text: string): string {
    const trailingNewline = /\r?\n$/.test(text);
    const visibleLines: string[] = [];
    const lines = text.split(/\r?\n/);
    lines.forEach((line, index) => {
      if (index === lines.length - 1 && line === "" && trailingNewline) {
        return;
      }
      if (parseStructuredEvent(line)) {
        return;
      }
      visibleLines.push(line);
    });
    if (!visibleLines.length) {
      return "";
    }
    return `${visibleLines.join("\n")}${trailingNewline ? "\n" : ""}`;
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

  async function startJob(request: DesktopJobRequest): Promise<void> {
    try {
      setBusy(true);
      setStatus("启动中");
      resetProgress(request.command);
      appendLog(`\n$ subtitle-llm ${request.command}\n`);
      const response = await api.startJob(request);
      activeJobId.value = response.jobId;
      activeCommand.value = request.command;
    } catch (error) {
      setBusy(false);
      setStatus("启动失败");
      finishJobProgress(false);
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
    await startJob({
      command: "translate",
      options: {
        input,
        targetLanguage: cleanString(translateForm.targetLanguage),
        sourceLanguage: cleanString(translateForm.sourceLanguage),
        output,
        config: translateForm.useYamlConfig ? cleanString(translateForm.config) : "",
        outputFormat: translateForm.outputFormat,
        reviewMode: translateForm.reviewMode,
        refineTranslation: translateForm.refineTranslation,
        resume: translateForm.resume,
        embedVideo,
        video,
      },
      modelSelection: translateForm.useYamlConfig ? null : modelSelection(),
    });
  }

  async function submitMux(): Promise<void> {
    await startJob({
      command: "mux",
      options: {
        subtitle: cleanString(muxForm.subtitle),
        video: cleanString(muxForm.video),
        targetLanguage: cleanString(muxForm.targetLanguage) || cleanString(translateForm.targetLanguage) || "Chinese",
      },
    });
  }

  async function submitDownload(): Promise<void> {
    await startJob({
      command: "download",
      options: {
        url: cleanString(downloadForm.url),
        outputDir: cleanString(downloadForm.outputDir),
        sourceLanguage: cleanString(downloadForm.sourceLanguage),
      },
    });
  }

  async function submitTranscribe(): Promise<void> {
    const output = cleanString(transcribeForm.output);
    setOutputPath(output);
    await startJob({
      command: "transcribe",
      options: {
        audio: cleanString(transcribeForm.audio),
        output,
        language: cleanString(transcribeForm.language),
        config: cleanString(transcribeForm.config),
      },
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
    const filePath = resultPath(target);
    if (filePath) {
      await api.openPath(filePath);
    }
  }

  async function showResult(target: ResultTarget): Promise<void> {
    const filePath = resultPath(target);
    if (filePath) {
      await api.showInFolder(filePath);
    }
  }

  function resultPath(target: ResultTarget): string {
    if (target === "video") {
      return lastEmbeddedVideoPath.value;
    }
    if (target === "trace") {
      return lastLlmTraceDir.value;
    }
    return lastSubtitlePath.value;
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
      resetProgress(event.command);
      lastLogStage = "";
      appendLog(`Python: ${event.pythonExecutable}\n工作目录: ${event.cwd}\n`);
      if (event.generatedConfigPath) {
        appendLog(`模型配置: ${event.generatedConfigPath}\n`);
      }
    } else if (event.type === "stdout") {
      const visibleText = consumeStructuredStdout(event.text);
      if (visibleText) {
        appendLog(visibleText);
      }
    } else if (event.type === "stderr") {
      appendLog(event.text, "stderr");
    } else if (event.type === "error") {
      setBusy(false);
      setStatus("失败");
      finishJobProgress(false);
      appendLog(`${event.message}\n`, "stderr");
    } else if (event.type === "finished") {
      setBusy(false);
      setStatus(event.code === 0 ? "完成" : `退出码 ${event.code}`);
      finishJobProgress(event.code === 0);
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

  return {
    activeTab,
    apiKeyDrafts,
    appState,
    canStartMux,
    canStartTranslate,
    cancelJob,
    chunkStatusLabel,
    chunkTooltip,
    chooseAudio,
    configDrawerOpen,
    chooseDownloadDir,
    chooseInput,
    chooseMuxSubtitle,
    chooseMuxVideo,
    chooseOutput,
    chooseTranscribeConfig,
    chooseTranscribeOutput,
    chooseTranslateConfig,
    chooseTranslateVideo,
    clearLog,
    clearSettingsKey,
    configureProvider,
    configureProviderText,
    customModelInput,
    downloadForm,
    drawerCloseIcon: X,
    drawerSubtitle,
    ffmpegAvailable,
    hasAnyResult,
    hasSubtitleResult,
    hasTraceResult,
    hasVideoResult,
    isBusy,
    lastEmbeddedVideoPath,
    lastLlmTraceDir,
    lastOutputPath,
    lastSourceVideoPath,
    lastSubtitlePath,
    logBody,
    logText,
    mkvCapabilityClass,
    mkvCapabilityText,
    muxForm,
    onboardingApiKey,
    onboardingKeyLabel,
    onEmbedMkvChanged,
    onModelChanged,
    onProviderChanged,
    onTranslateInput,
    openOutput,
    openResult,
    openSettingsFromOnboarding,
    providerCredentialStatus,
    providerModels,
    providers,
    persistProviderPreference,
    progressChunkSummary,
    progressChunks,
    progressLongWaitHint,
    progressStages,
    progressState,
    progressWaitText,
    runStatus,
    runtimeInfo,
    saveOnboardingKey,
    saveSettingsKey,
    selectChunk,
    selectedModelId,
    selectedOnboardingProviderId,
    selectedProviderId,
    selectedProgressChunk,
    selectOnboardingProvider,
    setActiveTab,
    showConfigureProvider,
    showCustomModelInput,
    showOnboarding,
    showOutput,
    showResult,
    statusPillClass,
    submitDownload,
    submitMux,
    submitTranscribe,
    submitTranslate,
    taskTabs,
    transcribeForm,
    translateForm,
    visibleOnboardingProviders,
    toggleConfigDrawer,
  };
}
