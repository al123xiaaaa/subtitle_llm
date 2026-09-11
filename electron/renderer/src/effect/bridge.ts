import { Context, Effect, Layer } from "effect";
import type {
  AppState,
  DesktopJobRequest,
  DesktopPreferences,
  JobStartResponse,
  ProviderModel,
  ReusableSubtitleMatch,
  ShellResult,
  TranslationTaskSummary,
} from "../../../types";
import { BridgeError } from "./errors";

// preload 暴露的 window.subtitleLLM 的 Effect 化封装。
// 每个方法对应一次 IPC 调用，失败统一映射为 BridgeError；onJobEvent 属于订阅，
// 不走这里，见 jobEvents.ts。
export interface BridgeService {
  readonly getState: () => Effect.Effect<AppState, BridgeError>;
  readonly saveApiKey: (providerId: string, apiKey: string) => Effect.Effect<AppState, BridgeError>;
  readonly clearApiKey: (providerId: string) => Effect.Effect<AppState, BridgeError>;
  readonly savePreferences: (preferences: DesktopPreferences) => Effect.Effect<AppState, BridgeError>;
  readonly selectInput: () => Effect.Effect<string | null, BridgeError>;
  readonly selectAudio: () => Effect.Effect<string | null, BridgeError>;
  readonly selectVideo: () => Effect.Effect<string | null, BridgeError>;
  readonly selectSubtitle: () => Effect.Effect<string | null, BridgeError>;
  readonly selectConfig: () => Effect.Effect<string | null, BridgeError>;
  readonly selectDirectory: () => Effect.Effect<string | null, BridgeError>;
  readonly saveSrt: (defaultName?: string) => Effect.Effect<string | null, BridgeError>;
  readonly startJob: (request: DesktopJobRequest) => Effect.Effect<JobStartResponse, BridgeError>;
  readonly cancelJob: (jobId: string) => Effect.Effect<ShellResult, BridgeError>;
  readonly listTranslationTasks: (includeDeleted?: boolean) => Effect.Effect<TranslationTaskSummary[], BridgeError>;
  readonly findReusableSubtitle: (sourceUrl: string) => Effect.Effect<ReusableSubtitleMatch | null, BridgeError>;
  readonly softDeleteTranslationTask: (taskId: string) => Effect.Effect<ShellResult, BridgeError>;
  readonly restoreTranslationTask: (taskId: string) => Effect.Effect<ShellResult, BridgeError>;
  readonly openPath: (filePath: string) => Effect.Effect<ShellResult, BridgeError>;
  readonly showInFolder: (filePath: string) => Effect.Effect<ShellResult, BridgeError>;
  readonly fetchProviderModels: (providerId: string) => Effect.Effect<ProviderModel[], BridgeError>;
}

export class Bridge extends Context.Tag("subtitle-llm/Bridge")<Bridge, BridgeService>() {}

const wrap = <A>(method: string, promise: Promise<A>): Effect.Effect<A, BridgeError> =>
  Effect.tryPromise({
    try: () => promise,
    catch: (error) =>
      new BridgeError({
        method,
        message: error instanceof Error ? error.message : String(error),
      }),
  });

const withBridge = <A>(method: keyof BridgeService, call: (bridge: Window["subtitleLLM"]) => Promise<A>) =>
  wrap(method, call(window.subtitleLLM));

export const BridgeLive: Layer.Layer<Bridge> = Layer.succeed(Bridge, {
  getState: () => withBridge("getState", (api) => api.getState()),
  saveApiKey: (providerId, apiKey) => withBridge("saveApiKey", (api) => api.saveApiKey(providerId, apiKey)),
  clearApiKey: (providerId) => withBridge("clearApiKey", (api) => api.clearApiKey(providerId)),
  savePreferences: (preferences) => withBridge("savePreferences", (api) => api.savePreferences(preferences)),
  selectInput: () => withBridge("selectInput", (api) => api.selectInput()),
  selectAudio: () => withBridge("selectAudio", (api) => api.selectAudio()),
  selectVideo: () => withBridge("selectVideo", (api) => api.selectVideo()),
  selectSubtitle: () => withBridge("selectSubtitle", (api) => api.selectSubtitle()),
  selectConfig: () => withBridge("selectConfig", (api) => api.selectConfig()),
  selectDirectory: () => withBridge("selectDirectory", (api) => api.selectDirectory()),
  saveSrt: (defaultName) => withBridge("saveSrt", (api) => api.saveSrt(defaultName)),
  startJob: (request) => withBridge("startJob", (api) => api.startJob(request)),
  cancelJob: (jobId) => withBridge("cancelJob", (api) => api.cancelJob(jobId)),
  listTranslationTasks: (includeDeleted) => withBridge("listTranslationTasks", (api) => api.listTranslationTasks(includeDeleted)),
  findReusableSubtitle: (sourceUrl) => withBridge("findReusableSubtitle", (api) => api.findReusableSubtitle(sourceUrl)),
  softDeleteTranslationTask: (taskId) => withBridge("softDeleteTranslationTask", (api) => api.softDeleteTranslationTask(taskId)),
  restoreTranslationTask: (taskId) => withBridge("restoreTranslationTask", (api) => api.restoreTranslationTask(taskId)),
  openPath: (filePath) => withBridge("openPath", (api) => api.openPath(filePath)),
  showInFolder: (filePath) => withBridge("showInFolder", (api) => api.showInFolder(filePath)),
  fetchProviderModels: (providerId) => withBridge("fetchProviderModels", (api) => api.fetchProviderModels(providerId)),
});
