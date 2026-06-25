import { computed, onMounted, ref } from "vue";
import type { ModelSelection, ProviderSummary } from "../../../types";
import { useAppShell } from "./useAppShell";
import { useJobLifecycle } from "./useJobLifecycle";
import { useProviderSettings } from "./useProviderSettings";
import { useTaskForms } from "./useTaskForms";
import { cleanString, looksLikeUrl } from "./controllerUtils";

export function useAppController() {
  const api = window.subtitleLLM;
  const isBusy = ref(false);
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
    const output = cleanString(taskForms.translateForm.output);
    if (output) {
      jobLifecycle.setSubtitlePath(output);
    }
    await providerState.persistProviderPreference();
    const input = cleanString(taskForms.translateForm.input);
    const video = cleanString(taskForms.translateForm.video);
    const embedVideo = taskForms.translateForm.embedMkv && providerState.ffmpegAvailable.value && (looksLikeUrl(input) || Boolean(video));
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
      },
    });
  }

  function modelSelection(): ModelSelection {
    return providerState.modelSelection();
  }

  const formActions = {
    canStartMux,
    canStartTranslate,
    submitDownload,
    submitMux,
    submitTranscribe,
    submitTranslate,
  };

  const onboarding = {
    onboardingApiKey: providerState.onboardingApiKey,
    onboardingKeyLabel: providerState.onboardingKeyLabel,
    openSettingsFromOnboarding: providerState.openSettingsFromOnboarding,
    saveOnboardingKey: providerState.saveOnboardingKey,
    selectedOnboardingProviderId: providerState.selectedOnboardingProviderId,
    selectOnboardingProvider: providerState.selectOnboardingProvider,
    showOnboarding: providerState.showOnboarding,
    visibleOnboardingProviders: providerState.visibleOnboardingProviders,
  };

  onMounted(async () => {
    try {
      providerState.updateAppState(await api.getState());
      taskForms.syncEmbedDefault();
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
    shell,
  };
}
