import { computed, reactive, watch } from "vue";
import type { ComputedRef, Ref } from "vue";
import type { AppState } from "../../../types";
import type { DownloadFormState, MuxFormState, TranscribeFormState, TranslateFormState } from "./controllerTypes";
import { cleanString, looksLikeUrl } from "./controllerUtils";
import { appRuntime } from "../effect/runtime";
import { pickPath } from "../effect/programs/forms";

interface TaskFormsOptions {
  appState: Ref<AppState | null>;
  ffmpegAvailable: ComputedRef<boolean>;
}

export function useTaskForms(options: TaskFormsOptions) {
  const embedPreferenceTouched = reactive({ value: false });
  const translateForm = reactive<TranslateFormState>({
    semanticCheck: true,
    input: "",
    targetLanguage: "Chinese",
    sourceLanguage: "en",
    outputFormat: "",
    reviewMode: "auto",
    output: "",
    config: "",
    useYamlConfig: false,
    refineTranslation: false,
    forceAsr: false,
    asrModel: "",
    asrDevice: "",
    resume: false,
    embedMkv: false,
    video: "",
  });
  watch(options.appState, (next, previous) => {
    if (next && !previous) {
      translateForm.semanticCheck = next.preferences.semanticQuality !== 'off';
      translateForm.asrModel = next.preferences.asrModel || '';
      translateForm.asrDevice = next.preferences.asrDevice || '';
    }
  });

  const muxForm = reactive<MuxFormState>({
    subtitle: "",
    video: "",
    targetLanguage: "Chinese",
  });

  const downloadForm = reactive<DownloadFormState>({
    url: "",
    sourceLanguage: "en",
    outputDir: "data/input",
  });

  const transcribeForm = reactive<TranscribeFormState>({
    audio: "",
    language: "English",
    output: "",
    config: "",
    asrModel: "",
    asrDevice: "",
  });

  const asrModels = computed(() => options.appState.value?.asrModels || []);
  const defaultAsrModel = computed(() => options.appState.value?.defaultAsrModel || "");

  // transcribe.cpp 系（whisper 族）自动选最优后端（Mac 上 Metal），
  // 设备下拉只对 funasr 系有意义。
  function isAsrDeviceAuto(modelId: string): boolean {
    const id = modelId || defaultAsrModel.value;
    return asrModels.value.find((model) => model.id === id)?.backend === "transcribe-cpp";
  }

  const mkvCapabilityText = computed(() => {
    if (!options.appState.value) {
      return "检测 FFmpeg 中";
    }
    return options.ffmpegAvailable.value ? "FFmpeg 可用" : options.appState.value.ffmpeg?.error || "未找到 FFmpeg";
  });
  const mkvCapabilityClass = computed(() => (options.ffmpegAvailable.value ? "status-pill is-saved" : "status-pill is-missing"));
  const canStartMux = computed(
    () => options.ffmpegAvailable.value && Boolean(cleanString(muxForm.subtitle)) && Boolean(cleanString(muxForm.video)),
  );

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

  function syncEmbedDefault(): void {
    if (embedPreferenceTouched.value) {
      return;
    }
    translateForm.embedMkv = looksLikeUrl(cleanString(translateForm.input));
  }

  async function chooseInput(): Promise<void> {
    await appRuntime.runPromise(
      pickPath((bridge) => bridge.selectInput(), (filePath) => {
        translateForm.input = filePath;
      }),
    );
    syncEmbedDefault();
  }

  async function chooseTranslateVideo(): Promise<void> {
    await appRuntime.runPromise(
      pickPath((bridge) => bridge.selectVideo(), (filePath) => {
        translateForm.video = filePath;
        translateForm.embedMkv = true;
        embedPreferenceTouched.value = true;
        muxForm.video = filePath;
      }),
    );
  }

  async function chooseTranslateConfig(): Promise<void> {
    await appRuntime.runPromise(
      pickPath((bridge) => bridge.selectConfig(), (filePath) => {
        translateForm.config = filePath;
      }),
    );
  }

  async function chooseTranscribeConfig(): Promise<void> {
    await appRuntime.runPromise(
      pickPath((bridge) => bridge.selectConfig(), (filePath) => {
        transcribeForm.config = filePath;
      }),
    );
  }

  async function chooseDownloadDir(): Promise<void> {
    await appRuntime.runPromise(
      pickPath((bridge) => bridge.selectDirectory(), (filePath) => {
        downloadForm.outputDir = filePath;
      }),
    );
  }

  async function chooseMuxSubtitle(): Promise<void> {
    await appRuntime.runPromise(
      pickPath((bridge) => bridge.selectSubtitle(), (filePath) => {
        muxForm.subtitle = filePath;
      }),
    );
  }

  async function chooseMuxVideo(): Promise<void> {
    await appRuntime.runPromise(
      pickPath((bridge) => bridge.selectVideo(), (filePath) => {
        muxForm.video = filePath;
      }),
    );
  }

  async function chooseAudio(): Promise<void> {
    await appRuntime.runPromise(
      pickPath((bridge) => bridge.selectAudio(), (filePath) => {
        transcribeForm.audio = filePath;
        if (!cleanString(transcribeForm.output)) {
          transcribeForm.output = filePath.replace(/\.[^.]+$/, ".srt");
        }
      }),
    );
  }

  async function chooseOutput(): Promise<void> {
    const defaultName = cleanString(translateForm.input).replace(/\.[^.]+$/, ".zh.srt") || "output.zh.srt";
    await appRuntime.runPromise(
      pickPath((bridge) => bridge.saveSrt(defaultName), (filePath) => {
        translateForm.output = filePath;
      }),
    );
  }

  async function chooseTranscribeOutput(): Promise<void> {
    const defaultName = cleanString(transcribeForm.audio).replace(/\.[^.]+$/, ".srt") || "transcript.srt";
    await appRuntime.runPromise(
      pickPath((bridge) => bridge.saveSrt(defaultName), (filePath) => {
        transcribeForm.output = filePath;
      }),
    );
  }

  function onTranslateInput(): void {
    syncEmbedDefault();
  }

  function onEmbedMkvChanged(): void {
    embedPreferenceTouched.value = true;
  }

  return {
    asrModels,
    canStartMux,
    chooseAudio,
    chooseDownloadDir,
    chooseInput,
    chooseMuxSubtitle,
    chooseMuxVideo,
    chooseOutput,
    chooseTranscribeConfig,
    chooseTranscribeOutput,
    chooseTranslateConfig,
    chooseTranslateVideo,
    defaultAsrModel,
    downloadForm,
    isAsrDeviceAuto,
    mkvCapabilityClass,
    mkvCapabilityText,
    muxForm,
    onEmbedMkvChanged,
    onTranslateInput,
    syncEmbedDefault,
    transcribeForm,
    translateForm,
  };
}
