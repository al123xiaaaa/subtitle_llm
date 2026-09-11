export type CredentialSource = "saved" | "env" | "missing";

export interface CredentialStatus {
  source: CredentialSource;
  label: string;
  available: boolean;
  envKey: string;
}

export interface ProviderModel {
  id: string;
  label: string;
  description: string;
}

export interface ProviderDefinition {
  id: string;
  name: string;
  envKey: string;
  configProvider: string;
  endpoint: string;
  defaultModel: string;
  models: ProviderModel[];
  /** 为 true 时模型列表通过 {endpoint}/models 动态拉取，静态 models 作为失败兜底 */
  dynamicModels?: boolean;
}

export interface ProviderSummary extends ProviderDefinition {
  credential: CredentialStatus;
}

export interface DesktopPreferences {
  lastProviderId: string;
  modelsByProvider: Record<string, string>;
  customModelsByProvider: Record<string, string>;
}

export interface DesktopSettings {
  version: number;
  apiKeys: Record<string, string>;
  preferences: DesktopPreferences;
}

export interface FfmpegStatus {
  available: boolean;
  executable: string;
  version: string;
  error: string;
}

export interface AppState {
  projectRoot: string;
  pythonExecutable: string;
  hasMainPy: boolean;
  settingsPath: string;
  ffmpeg: FfmpegStatus;
  providers: ProviderSummary[];
  preferences: DesktopPreferences;
  hasAnyCredential: boolean;
  preferredProviderId: string;
  asrModels: AsrModelOption[];
  defaultAsrModel: string;
}

export interface AsrModelOption {
  id: string;
  label: string;
  description: string;
  /** funasr 系才有设备选择；transcribe-cpp 自动选最优后端（Mac 上 Metal） */
  backend?: string;
}

export type CommandName = "translate" | "download" | "transcribe" | "mux";
export type OutputFormat = "source-first" | "target-first" | "target-only" | "source-only" | "bilingual";
export type ReviewMode = "auto" | "config" | "tui";
export type ProgressStageStatus = "waiting" | "running" | "done" | "warning" | "failed" | "skipped";
export type ChunkProgressStatus =
  | "waiting"
  | "running"
  | "review"
  | "repairing"
  | "done"
  | "warning"
  | "failed"
  | "skipped";

export interface ModelSelection {
  mode: "service";
  providerId: string;
  modelId: string;
  customModelId?: string;
  endpoint?: string;
}

export interface TranslateJobOptions {
  input?: string;
  targetLanguage?: string;
  taskId?: string;
  sourceLanguage?: string;
  output?: string;
  config?: string;
  outputFormat?: OutputFormat | "";
  reviewMode?: ReviewMode;
  refineTranslation?: boolean;
  forceAsr?: boolean;
  reuseSubtitle?: string;
  asrModel?: string;
  asrDevice?: string;
  resume?: boolean;
  embedVideo?: boolean;
  video?: string;
  videoOutput?: string;
  ffmpeg?: string;
}

export interface DownloadJobOptions {
  url: string;
  outputDir?: string;
  sourceLanguage?: string;
}

export interface TranscribeJobOptions {
  audio: string;
  output: string;
  language?: string;
  config?: string;
  asrModel?: string;
  asrDevice?: string;
}

export interface MuxJobOptions {
  video: string;
  subtitle: string;
  output?: string;
  targetLanguage?: string;
  ffmpeg?: string;
}

export interface TranslateJobRequest {
  command: "translate";
  options: TranslateJobOptions;
  modelSelection?: ModelSelection | null;
  envOverrides?: Record<string, string>;
}

export interface DownloadJobRequest {
  command: "download";
  options: DownloadJobOptions;
  envOverrides?: Record<string, string>;
}

export interface TranscribeJobRequest {
  command: "transcribe";
  options: TranscribeJobOptions;
  envOverrides?: Record<string, string>;
}

export interface MuxJobRequest {
  command: "mux";
  options: MuxJobOptions;
  envOverrides?: Record<string, string>;
}

export type DesktopJobRequest = TranslateJobRequest | DownloadJobRequest | TranscribeJobRequest | MuxJobRequest;

export type PreparedDesktopJobRequest = DesktopJobRequest & {
  envOverrides: Record<string, string>;
  generatedConfigPath?: string;
};

export interface JobStartResponse {
  jobId: string;
}

export type JobEvent =
  | {
      type: "started";
      jobId: string;
      command: CommandName;
      pythonExecutable: string;
      cwd: string;
      generatedConfigPath: string;
    }
  | {
      type: "stdout" | "stderr";
      jobId: string;
      text: string;
    }
  | {
      type: "progress";
      jobId: string;
      event: CliProgressEvent;
    }
  | {
      type: "result";
      jobId: string;
      event: CliResultEvent;
    }
  | {
      type: "error";
      jobId: string;
      message: string;
    }
  | {
      type: "finished";
      jobId: string;
      code: number | null;
      signal: string | null;
    };

export interface CliResultEvent {
  command: CommandName;
  output_file?: string | null;
  source_video_file?: string | null;
  embedded_video_file?: string | null;
  output_video_file?: string | null;
  embedded_video_error?: string | null;
  context_file?: string | null;
  task_id?: string | null;
  task_db_file?: string | null;
  llm_trace_dir?: string | null;
  output_format?: OutputFormat | string | null;
}

export interface CliProgressChunk {
  index?: number | null;
  total?: number | null;
  entry_start?: number | null;
  entry_end?: number | null;
  entry_count?: number | null;
  status?: ChunkProgressStatus | string | null;
  detail?: string | null;
  issue_summary?: string | null;
  reason?: string | null;
}

export interface CliProgressModel {
  provider?: string | null;
  name?: string | null;
  endpoint?: string | null;
}

export interface CliProgressUsage {
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
}

export interface CliProgressRunUsage {
  call_count?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  call_duration_ms?: number | null;
}

export interface CliProgressEvent {
  command: CommandName;
  stage: string;
  detail: string;
  status?: ProgressStageStatus | string | null;
  label?: string | null;
  message?: string | null;
  elapsed_ms?: number | null;
  duration_ms?: number | null;
  created_at?: string | null;
  chunk?: CliProgressChunk | null;
  model?: CliProgressModel | null;
  usage?: CliProgressUsage | null;
  run_usage?: CliProgressRunUsage | null;
  trace_id?: string | null;
  total_chunks?: number | null;
}

export interface ShellResult {
  ok: boolean;
  error?: string;
  message?: string;
}

export interface TranslationTaskSummary {
  task_id: string;
  status: string;
  input_display: string;
  working_directory: string;
  source_url?: string | null;
  source_subtitle_path: string;
  target_language: string;
  source_language: string;
  output_format: string;
  output_file: string;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
  context_file?: string | null;
  llm_trace_dir?: string | null;
  source_video_file?: string | null;
}

export interface ReusableSubtitleMatch {
  taskId: string;
  sourceUrl: string;
  subtitlePath: string;
  videoPath: string;
  title: string;
  sourceLanguage: string;
  createdAt: string;
}

export interface SubtitleLlmBridge {
  getState: () => Promise<AppState>;
  saveApiKey: (providerId: string, apiKey: string) => Promise<AppState>;
  clearApiKey: (providerId: string) => Promise<AppState>;
  savePreferences: (preferences: DesktopPreferences) => Promise<AppState>;
  selectInput: () => Promise<string | null>;
  selectAudio: () => Promise<string | null>;
  selectVideo: () => Promise<string | null>;
  selectSubtitle: () => Promise<string | null>;
  selectConfig: () => Promise<string | null>;
  selectDirectory: () => Promise<string | null>;
  saveSrt: (defaultName?: string) => Promise<string | null>;
  startJob: (request: DesktopJobRequest) => Promise<JobStartResponse>;
  cancelJob: (jobId: string) => Promise<ShellResult>;
  listTranslationTasks: (includeDeleted?: boolean) => Promise<TranslationTaskSummary[]>;
  findReusableSubtitle: (sourceUrl: string) => Promise<ReusableSubtitleMatch | null>;
  softDeleteTranslationTask: (taskId: string) => Promise<ShellResult>;
  restoreTranslationTask: (taskId: string) => Promise<ShellResult>;
  openPath: (filePath: string) => Promise<ShellResult>;
  showInFolder: (filePath: string) => Promise<ShellResult>;
  fetchProviderModels: (providerId: string) => Promise<ProviderModel[]>;
  onJobEvent: (callback: (event: JobEvent) => void) => () => void;
}
