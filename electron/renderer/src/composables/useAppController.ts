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
  // 点历史卡片后的"查看态"：RunPanel 显示该记录的完成态结果。
  // 运行任务、返回待命时清空，避免详情残留成假状态。
  const inspectedRecord = ref<TranslationTaskSummary | null>(null);
  const shell = useAppShell(isBusy);
  let taskForms = {} as ReturnType<typeof useTaskForms>;
  let jobLifecycle = {} as ReturnType<typeof useJobLifecycle>;
  const providerState = useProviderSettings({
    getTaskRoleSettings: () => ({semanticQuality: taskForms.translateForm.semanticCheck ? 'jev' : 'off',
      asrModel: taskForms.translateForm.asrModel, asrDevice: taskForms.translateForm.asrDevice}),
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
      (taskForms.translateForm.useYamlConfig || (Boolean(selectedProvider?.credential.available) && !providerState.outputBudgetError.value))
    );
  });
  const canStartMux = computed(() => !isBusy.value && taskForms.canStartMux.value);

  async function submitTranslate(): Promise<void> {
    inspectedRecord.value = null;
    await startTranslate();
  }

  const translationSubmitting = ref(false);

  async function startTranslate(): Promise<void> {
    if (translationSubmitting.value || !canStartTranslate.value) return;
    const form = { ...taskForms.translateForm };
    const input = cleanString(form.input);
    if (!form.useYamlConfig && form.semanticCheck && providerState.appState.value?.gatewayCredentialState === 'missing') {
      jobLifecycle.appendLog('Jev 尚未配置凭据，请在本地配置，或明确选择“仅翻译，稍后补查”。\n', 'stderr');
      return;
    }
    if (!input || !cleanString(form.targetLanguage)) return;
    const match = reusableSubtitle.value;
    const reuse = reuseSourceSubtitle.value && match?.sourceUrl === input && match.subtitlePath ? match : null;
    if (!taskForms.translateForm.useYamlConfig && providerState.outputBudgetError.value) {
      jobLifecycle.appendLog(`${providerState.outputBudgetError.value}\n`, "stderr");
      return;
    }
    const output = cleanString(form.output);
    const video = cleanString(form.video) || reuse?.videoPath || "";
    if (form.embedMkv && (!providerState.ffmpegAvailable.value || (reuse && !video))) {
      jobLifecycle.appendLog('视频生成条件不足，请补齐视频和 FFmpeg，或明确选择“本次仅翻译字幕”。\n', 'stderr');
      return;
    }
    // 复用源字幕时不会下载视频，生成 MKV 必须已有本地视频。
    const embedVideo = form.embedMkv && providerState.ffmpegAvailable.value
      && (reuse ? Boolean(video) : looksLikeUrl(input) || Boolean(video));
    const selection = form.useYamlConfig ? null : {...modelSelection(), semanticQuality: form.semanticCheck ? "jev" as const : "off" as const};
    translationSubmitting.value = true;
    reusableController.dismiss();
    try {
      await jobLifecycle.startJob({
        command: "translate",
        options: {
          input,
          targetLanguage: cleanString(form.targetLanguage),
          sourceLanguage: cleanString(form.sourceLanguage),
          output,
          config: form.useYamlConfig ? cleanString(form.config) : "",
          outputFormat: form.outputFormat,
          reviewMode: form.reviewMode,
          refineTranslation: form.refineTranslation,
          forceAsr: reuse ? false : form.forceAsr,
          reuseSubtitle: reuse?.subtitlePath || "",
          asrModel: form.asrModel,
          asrDevice: form.asrDevice,
          resume: form.resume,
          embedVideo,
          video,
        },
        modelSelection: selection,
      });
    } catch (error) {
      jobLifecycle.appendLog(`${error instanceof Error ? error.message : String(error)}\n`, "stderr");
    } finally {
      translationSubmitting.value = false;
    }
  }

  watch(isBusy, (busy) => {
    if (busy) clearInspectedRecord();
  });

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
    inspectedRecord.value = null;
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

  function inspectTaskRecord(record: TranslationTaskSummary): void {
    if (isBusy.value) {
      return;
    }
    inspectedRecord.value = record;
  }

  function clearInspectedRecord(): void {
    inspectedRecord.value = null;
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
  // 找到就提示复用还是重新获取；选择只是意图，启动统一走"开始翻译"。
  const reuseSourceSubtitle = ref(true);
  const reusableSubtitle = ref<ReusableSubtitleMatch | null>(null);
  let reusableDismissedFor = "";
  const reusableController = makeReusableController({
    reusableSubtitle,
    getInput: () => taskForms.translateForm.input,
    isBusy: () => isBusy.value || translationSubmitting.value,
    getDismissedFor: () => reusableDismissedFor,
    setDismissedFor: (value) => {
      reusableDismissedFor = value;
    },
  });

  function dismissReusableSubtitle(): void {
    reusableController.dismiss();
  }

  const existingTranslation = computed(() => reusableSubtitle.value?.completedTasks.find(
    (task) => cleanString(task.target_language).toLowerCase() === cleanString(taskForms.translateForm.targetLanguage).toLowerCase(),
  ) ?? null);

  function inspectReusableResult(): void {
    if (existingTranslation.value && !isBusy.value) {
      inspectTaskRecord(existingTranslation.value);
      shell.configDrawerOpen.value = false;
    }
  }

  watch(
    () => taskForms.translateForm.input,
    (value) => {
      reuseSourceSubtitle.value = true;
      reusableController.onInputChanged(value);
    },
  );

  const formActions = {
    newTranslation: () => {
      const preferences = providerState.appState.value?.preferences;
      providerState.restoreTaskDefaults();
      Object.assign(taskForms.translateForm, {input:'', output:'', video:'', useYamlConfig:false,
        semanticCheck:preferences?.semanticQuality !== 'off', asrModel:preferences?.asrModel || '', asrDevice:preferences?.asrDevice || ''});
      shell.setActiveTab('translate');
    },
    canStartMux,
    canStartTranslate,
    dismissReusableSubtitle,
    inspectReusableResult,
    reusableSubtitle,
    submitDownload,
    submitMux,
    submitTranscribe,
    submitTranslate,
    reuseSourceSubtitle,
    existingTranslation,
    translationSubmitting,
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
    clearInspectedRecord,
    inspectTaskRecord,
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
    inspectedRecord,
    isBusy,
    job: jobLifecycle,
    onboarding,
    providerState,
    records,
    shell,
  };
}
