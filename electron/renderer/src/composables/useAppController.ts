import { computed, onMounted, ref, watch } from "vue";
import type { ModelSelection, ProviderSummary, ReusableSubtitleMatch, TranslationTaskSummary } from "../../../types";
import { useAppShell } from "./useAppShell";
import { useJobLifecycle } from "./useJobLifecycle";
import { useProviderSettings } from "./useProviderSettings";
import { useTaskForms } from "./useTaskForms";
import { cleanString, looksLikeUrl } from "./controllerUtils";
import { appRuntime, runBridge, runSilent } from "../effect/runtime";
import { makeReusableController } from "../effect/programs/reusable";
import {
  refreshTaskRecords as refreshTaskRecordsProgram,
  restoreTaskRecord as restoreTaskRecordProgram,
  softDeleteTaskRecord as softDeleteTaskRecordProgram,
} from "../effect/programs/records";
import type { TaskRecordsPorts } from "../effect/programs/records";

export function useAppController() {
  const isBusy = ref(false);
  const taskRecords = ref<TranslationTaskSummary[]>([]);
  const taskRecordsStatus = ref("加载中");
  const showDeletedTaskRecords = ref(false);
  const shell = useAppShell(isBusy);
  let taskForms = {} as ReturnType<typeof useTaskForms>;
  let jobLifecycle = {} as ReturnType<typeof useJobLifecycle>;
  const providerState = useProviderSettings({
    appendLog: (text, kind) => jobLifecycle.appendLog(text, kind),
    getUseYamlConfig: () => Boolean(taskForms.translateForm?.useYamlConfig),
    setActiveTab: shell.setActiveTab,
  });

  taskForms = useTaskForms({
    appState: providerState.appState,
    ffmpegAvailable: providerState.ffmpegAvailable,
  });

  jobLifecycle = useJobLifecycle({
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

  const recordsPorts: TaskRecordsPorts = {
    showDeletedTaskRecords,
    taskRecords,
    setStatus: (text) => {
      taskRecordsStatus.value = text;
    },
    appendLog: (text, kind) => jobLifecycle.appendLog(text, kind),
  };

  async function refreshTaskRecords(): Promise<void> {
    await appRuntime.runPromise(refreshTaskRecordsProgram(recordsPorts));
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
    await appRuntime.runPromise(softDeleteTaskRecordProgram(record.task_id, recordsPorts));
  }

  async function restoreTaskRecord(record: TranslationTaskSummary): Promise<void> {
    await appRuntime.runPromise(restoreTaskRecordProgram(record.task_id, recordsPorts));
  }

  async function openTaskOutput(record: TranslationTaskSummary): Promise<void> {
    if (record.output_file) {
      await runSilent((bridge) => bridge.openPath(record.output_file as string));
    }
  }

  async function showTaskOutput(record: TranslationTaskSummary): Promise<void> {
    if (record.output_file) {
      await runSilent((bridge) => bridge.showInFolder(record.output_file as string));
    }
  }

  async function openTaskSourceVideo(record: TranslationTaskSummary): Promise<void> {
    if (record.source_video_file) {
      await runSilent((bridge) => bridge.openPath(record.source_video_file as string));
    }
  }

  async function showTaskSourceVideo(record: TranslationTaskSummary): Promise<void> {
    if (record.source_video_file) {
      await runSilent((bridge) => bridge.showInFolder(record.source_video_file as string));
    }
  }

  async function toggleDeletedTaskRecords(): Promise<void> {
    showDeletedTaskRecords.value = !showDeletedTaskRecords.value;
    await refreshTaskRecords();
  }

  // 输入 URL 变化时去任务记录里找可复用的源字幕（上次下载/ASR 产物），
  // 找到就提示用户复用还是重新生成，避免重跑时白白再转写一遍。
  const reusableSubtitle = ref<ReusableSubtitleMatch | null>(null);
  let reusableDismissedFor = "";
  const reusableController = makeReusableController({
    reusableSubtitle,
    getInput: () => taskForms.translateForm.input,
    isBusy: () => isBusy.value,
    getDismissedFor: () => reusableDismissedFor,
    setDismissedFor: (value) => {
      reusableDismissedFor = value;
    },
  });

  function dismissReusableSubtitle(): void {
    reusableController.dismiss();
  }

  watch(
    () => taskForms.translateForm.input,
    (value) => {
      reusableController.onInputChanged(value);
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
      providerState.updateAppState(await runBridge((bridge) => bridge.getState()));
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
