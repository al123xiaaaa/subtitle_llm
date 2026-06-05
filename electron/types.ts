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
}

export type CommandName = "translate" | "download" | "transcribe" | "mux";

export interface ModelSelection {
  mode: "service";
  providerId: string;
  modelId: string;
  customModelId?: string;
  endpoint?: string;
}

export interface DesktopJobRequest {
  command: CommandName;
  options: Record<string, unknown>;
  modelSelection?: ModelSelection | null;
  envOverrides?: Record<string, string>;
}

export interface PreparedDesktopJobRequest extends DesktopJobRequest {
  envOverrides: Record<string, string>;
  generatedConfigPath?: string;
}

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

export interface ShellResult {
  ok: boolean;
  error?: string;
  message?: string;
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
  openPath: (filePath: string) => Promise<ShellResult>;
  showInFolder: (filePath: string) => Promise<ShellResult>;
  onJobEvent: (callback: (event: JobEvent) => void) => () => void;
}
