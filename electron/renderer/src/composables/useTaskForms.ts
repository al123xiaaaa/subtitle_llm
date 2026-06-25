import { computed, reactive, watch } from "vue";
import type { ComputedRef, Ref } from "vue";
import type { AppState } from "../../../types";
import type { DownloadFormState, MuxFormState, TranscribeFormState, TranslateFormState } from "./controllerTypes";
import { cleanString, looksLikeUrl } from "./controllerUtils";

interface TaskFormsOptions {
  appState: Ref<AppState | null>;
  ffmpegAvailable: ComputedRef<boolean>;
}

export function useTaskForms(api: Window["subtitleLLM"], options: TaskFormsOptions) {
  const embedPreferenceTouched = reactive({ value: false });
  const translateForm = reactive<TranslateFormState>({
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
    resume: false,
    embedMkv: false,
    video: "",
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
  });

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
    if (embedPreferenceTouched.value || !options.ffmpegAvailable.value) {
      return;
    }
    translateForm.embedMkv = looksLikeUrl(cleanString(translateForm.input));
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

  return {
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
    downloadForm,
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
