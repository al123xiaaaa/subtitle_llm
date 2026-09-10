import type { LucideIcon } from "@lucide/vue";
import type { OutputFormat, ReviewMode } from "../../../types";

export type TaskTab = "translate" | "download" | "transcribe" | "settings";
export type ResultTarget = "subtitle" | "trace" | "video" | "source";

export interface TaskTabMeta {
  id: TaskTab;
  label: string;
  subtitle: string;
  icon: LucideIcon;
}

export interface TranslateFormState {
  input: string;
  targetLanguage: string;
  sourceLanguage: string;
  outputFormat: OutputFormat | "";
  reviewMode: ReviewMode;
  output: string;
  config: string;
  useYamlConfig: boolean;
  refineTranslation: boolean;
  forceAsr: boolean;
  resume: boolean;
  embedMkv: boolean;
  video: string;
}

export interface MuxFormState {
  subtitle: string;
  video: string;
  targetLanguage: string;
}

export interface DownloadFormState {
  url: string;
  sourceLanguage: string;
  outputDir: string;
}

export interface TranscribeFormState {
  audio: string;
  language: string;
  output: string;
  config: string;
}
