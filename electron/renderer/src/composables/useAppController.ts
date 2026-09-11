import { computed, onMounted, ref, watch } from "vue";
import type { ModelSelection, ProviderSummary, ReusableSubtitleMatch, TranslationTaskSummary } from "../../../types";
import { useAppShell } from "./useAppShell";
import { useJobLifecycle } from "./useJobLifecycle";
import { useProviderSettings } from "./useProviderSettings";
import { useTaskForms } from "./useTaskForms";
import { cleanString, looksLikeUrl } from "./controllerUtils";

export function useAppController() {
  const api = window.subtitleLLM;
  const isBusy = ref(false);
  const taskRecords = ref<TranslationTaskSummary[]>([]);
  const taskRecordsStatus = ref("加载中");
  const showDeletedTaskRecords = ref(false);
  const shell = useAppShell(isBusy);
  let taskForms = {} as ReturnType<typeof useTaskForms>;
  let jobLifecycle = {} as ReturnType<typeof useJobLifecycle>;
  const providerState = useProviderSettings(api, {
    appendLog: (text, kind) => jobLifecycle.appendLog(text, kind),
    getUseYamlConfig: () => Boolean(taskForms.translateForm?.useYamlConfig),
    setActiveTab: shell.setActiveTab,
  });

  taskForms = useTaskForms(api, {
    appState: providerState.appState,
    ffmpegAvailable: providerState.ffmpegAvailable,
  });

  jobLifecycle = useJobLifecycle(api, {
    configDrawerOpen: shell.configDrawerOpen,
    isBusy,
    onSourceVideoPath: (filePath) => {
      taskForms.muxForm.video = filePath;
    },
    onSubtitlePath: (filePath) => {
      taskForms.muxForm.subtitle = filePath;
    },
  });

  const canStartTranslate = computed(() => {
    const selectedProvider = providerState.selectedProvider.value as ProviderSummary | null;
    return (
      !isBusy.value &&
      Boolean(selectedProvider) &&
      (taskForms.translateForm.useYamlConfig || Boolean(selectedProvider?.credential.available))
    );
  });
  const canStartMux = computed(() => !isBusy.value && taskForms.canStartMux.value);

  async function submitTranslate(): Promise<void> {
    await startTranslate(null);
  }

  async function submitTranslateReuse(): Promise<void> {
    await startTranslate(reusableSubtitle.value);
  }

  async function startTranslate(reuse: ReusableSubtitleMatch | null): Promise<void> {
    reusableSubtitle.value = null;
    const output = cleanString(taskForms.translateForm.output);
    if (output) {
      jobLifecycle.setSubtitlePath(output);
    }
    await providerState.persistProviderPreference();
    const input = cleanString(taskForms.translateForm.input);
    const video = reuse?.videoPath || cleanString(taskForms.translateForm.video);
    // 复用模式下 Python 不参与下载，无法自己解析视频，embed 依赖记录里的视频路径
    const embedVideo = taskForms.translateForm.embedMkv && providerState.ffmpegAvailable.value
      && (reuse ? Boolean(reuse.videoPath) : looksLikeUrl(input) || Boolean(video));
    await jobLifecycle.startJob({
      command: "translate",
      options: {
        input,
        targetLanguage: cleanString(taskForms.translateForm.targetLanguage),
        sourceLanguage: cleanString(taskForms.translateForm.sourceLanguage),
        output,
        config: taskForms.translateForm.useYamlConfig ? cleanString(taskForms.translateForm.config) : "",
        outputFormat: taskForms.translateForm.outputFormat,
        reviewMode: taskForms.translateForm.reviewMode,
        refineTranslation: taskForms.translateForm.refineTranslation,
        forceAsr: reuse ? false : taskForms.translateForm.forceAsr,
        reuseSubtitle: reuse?.subtitlePath || "",
        asrModel: taskForms.translateForm.asrModel,
        asrDevice: taskForms.translateForm.asrDevice,
        resume: taskForms.translateForm.resume,
        embedVideo,
        video,
      },
      modelSelection: taskForms.translateForm.useYamlConfig ? null : modelSelection(),
    });
  }

  async function submitMux(): Promise<void> {
    await jobLifecycle.startJob({
      command: "mux",
      options: {
        subtitle: cleanString(taskForms.muxForm.subtitle),
        video: cleanString(taskForms.muxForm.video),
        targetLanguage: cleanString(taskForms.muxForm.targetLanguage) || cleanString(taskForms.translateForm.targetLanguage) || "Chinese",
      },
    });
  }

  async function submitDownload(): Promise<void> {
    await jobLifecycle.startJob({
      command: "download",
      options: {
        url: cleanString(taskForms.downloadForm.url),
        outputDir: cleanString(taskForms.downloadForm.outputDir),
        sourceLanguage: cleanString(taskForms.downloadForm.sourceLanguage),
      },
    });
  }

  async function submitTranscribe(): Promise<void> {
    const output = cleanString(taskForms.transcribeForm.output);
    jobLifecycle.setOutputPath(output);
    await jobLifecycle.startJob({
      command: "transcribe",
      options: {
        audio: cleanString(taskForms.transcribeForm.audio),
        output,
        language: cleanString(taskForms.transcribeForm.language),
        config: cleanString(taskForms.transcribeForm.config),
        asrModel: taskForms.transcribeForm.asrModel,
        asrDevice: taskForms.transcribeForm.asrDevice,
      },
    });
  }

  function modelSelection(): ModelSelection {
    return providerState.modelSelection();
  }

  async function refreshTaskRecords(): Promise<void> {
    try {
      taskRecords.value = await api.listTranslationTasks(showDeletedTaskRecords.value);
      taskRecordsStatus.value = taskRecords.value.length ? "" : "暂无翻译任务记录";
    } catch (error) {
      taskRecordsStatus.value = error instanceof Error ? error.message : String(error);
    }
  }

  async function continueTaskRecord(record: TranslationTaskSummary): Promise<void> {
    if (isBusy.value) {
      return;
    }
    await jobLifecycle.startJob({
      command: "translate",
      options: {
        taskId: record.task_id,
        resume: true,
      },
      modelSelection: null,
    });
  }

  async function softDeleteTaskRecord(record: TranslationTaskSummary): Promise<void> {
    const result = await api.softDeleteTranslationTask(record.task_id);
    if (!result.ok) {
      jobLifecycle.appendLog(`${result.error || result.message || "删除任务记录失败"}\n`, "stderr");
    }
    await refreshTaskRecords();
  }

  async function restoreTaskRecord(record: TranslationTaskSummary): Promise<void> {
    const result = await api.restoreTranslationTask(record.task_id);
    if (!result.ok) {
      jobLifecycle.appendLog(`${result.error || result.message || "恢复任务记录失败"}\n`, "stderr");
    }
    await refreshTaskRecords();
  }

  async function openTaskOutput(record: TranslationTaskSummary): Promise<void> {
    if (record.output_file) {
      await api.openPath(record.output_file);
    }
  }

  async function showTaskOutput(record: TranslationTaskSummary): Promise<void> {
    if (record.output_file) {
      await api.showInFolder(record.output_file);
    }
  }

  async function openTaskSourceVideo(record: TranslationTaskSummary): Promise<void> {
    if (record.source_video_file) {
      await api.openPath(record.source_video_file);
    }
  }

  async function showTaskSourceVideo(record: TranslationTaskSummary): Promise<void> {
    if (record.source_video_file) {
      await api.showInFolder(record.source_video_file);
    }
  }

  async function toggleDeletedTaskRecords(): Promise<void> {
    showDeletedTaskRecords.value = !showDeletedTaskRecords.value;
    await refreshTaskRecords();
  }

  // 输入 URL 变化时去任务记录里找可复用的源字幕（上次下载/ASR 产物），
  // 找到就提示用户复用还是重新生成，避免重跑时白白再转写一遍。
  const reusableSubtitle = ref<ReusableSubtitleMatch | null>(null);
  let reusableQueryToken = 0;
  let reusableDismissedFor = "";
  let reusableQueryTimer: ReturnType<typeof setTimeout> | null = null;

  function dismissReusableSubtitle(): void {
    reusableDismissedFor = cleanString(taskForms.translateForm.input);
    reusableSubtitle.value = null;
  }

  async function queryReusableSubtitle(url: string): Promise<void> {
    const token = ++reusableQueryToken;
    try {
      const match = await api.findReusableSubtitle(url);
      if (token !== reusableQueryToken || cleanString(taskForms.translateForm.input) !== url) {
        return;
      }
      reusableSubtitle.value = match && url !== reusableDismissedFor ? match : null;
    } catch {
      if (token === reusableQueryToken) {
        reusableSubtitle.value = null;
      }
    }
  }

  watch(
    () => taskForms.translateForm.input,
    (value) => {
      if (reusableQueryTimer) {
        clearTimeout(reusableQueryTimer);
        reusableQueryTimer = null;
      }
      const url = cleanString(value);
      if (!looksLikeUrl(url) || isBusy.value) {
        reusableQueryToken += 1;
        reusableSubtitle.value = null;
        return;
      }
      reusableQueryTimer = setTimeout(() => {
        reusableQueryTimer = null;
        void queryReusableSubtitle(url);
      }, 400);
    },
  );

  const formActions = {
    canStartMux,
    canStartTranslate,
    dismissReusableSubtitle,
    reusableSubtitle,
    submitDownload,
    submitMux,
    submitTranscribe,
    submitTranslate,
    submitTranslateReuse,
  };

  const onboarding = {
    dismissOnboarding: providerState.dismissOnboarding,
    onboardingApiKey: providerState.onboardingApiKey,
    onboardingKeyLabel: providerState.onboardingKeyLabel,
    openSettingsFromOnboarding: providerState.openSettingsFromOnboarding,
    saveOnboardingKey: providerState.saveOnboardingKey,
    selectedOnboardingProviderId: providerState.selectedOnboardingProviderId,
    selectOnboardingProvider: providerState.selectOnboardingProvider,
    showOnboarding: providerState.showOnboarding,
    visibleOnboardingProviders: providerState.visibleOnboardingProviders,
  };

  const records = {
    continueTaskRecord,
    openTaskOutput,
    openTaskSourceVideo,
    refreshTaskRecords,
    restoreTaskRecord,
    showDeletedTaskRecords,
    showTaskOutput,
    showTaskSourceVideo,
    softDeleteTaskRecord,
    taskRecords,
    taskRecordsStatus,
    toggleDeletedTaskRecords,
  };

  onMounted(async () => {
    try {
      providerState.updateAppState(await api.getState());
      taskForms.syncEmbedDefault();
      await refreshTaskRecords();
    } catch (error) {
      jobLifecycle.appendLog(`${error instanceof Error ? error.message : String(error)}\n`, "stderr");
    }
  });

  return {
    formActions,
    forms: taskForms,
    isBusy,
    job: jobLifecycle,
    onboarding,
    providerState,
    records,
    shell,
  };
}
