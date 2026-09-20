import type { DesktopJobRequest, FfmpegStatus, PreparedDesktopJobRequest } from "../types.js";
import { getProvider } from "./providerCatalog.js";
import { writeDesktopModelConfig } from "./modelConfig.js";
import { resolveCredential, savedCredentialEnvOverrides } from "./settingsStore.js";

export interface DesktopTaskIntentContext {
  projectRoot: string;
  settingsPath: string;
  env: NodeJS.ProcessEnv;
  detectFfmpeg: () => FfmpegStatus;
}

export function prepareDesktopTaskIntent(
  request: DesktopJobRequest,
  context: DesktopTaskIntentContext,
): PreparedDesktopJobRequest {
  const nextRequest = cloneDesktopJobRequest(request);

  if (nextRequest.command === "translate") {
    nextRequest.envOverrides = {
      ...savedCredentialEnvOverrides(context.settingsPath),
      ...nextRequest.envOverrides,
    };
  }

  if (nextRequest.command === "translate" && nextRequest.modelSelection?.mode === "service") {
    const provider = getProvider(nextRequest.modelSelection.providerId);
    const credential = resolveCredential(context.settingsPath, provider, context.env);
    const summaryCredential = nextRequest.modelSelection.summaryModelId
      ? resolveCredential(context.settingsPath, getProvider(nextRequest.modelSelection.summaryProviderId || provider.id), context.env)
      : credential;
    nextRequest.generatedConfigPath = writeDesktopModelConfig(context.projectRoot, nextRequest.modelSelection, {
      configDir: context.env.SUBTITLE_LLM_DESKTOP_CONFIG_DIR || undefined,
    });
    nextRequest.options.config = nextRequest.generatedConfigPath;
    nextRequest.envOverrides = {
      ...nextRequest.envOverrides,
      ...credential.envOverrides,
      ...summaryCredential.envOverrides,
    };
  }

  if (nextRequest.command === "translate" || nextRequest.command === "mux") {
    const ffmpeg = context.detectFfmpeg();
    if (ffmpeg.available && !nextRequest.options.ffmpeg) {
      nextRequest.options.ffmpeg = ffmpeg.executable;
    }
  }

  return nextRequest;
}

export function cloneDesktopJobRequest(request: DesktopJobRequest): PreparedDesktopJobRequest {
  const envOverrides = { ...request.envOverrides };
  switch (request.command) {
    case "translate":
      return { ...request, options: { ...request.options }, envOverrides };
    case "download":
      return { ...request, options: { ...request.options }, envOverrides };
    case "transcribe":
      return { ...request, options: { ...request.options }, envOverrides };
    case "mux":
      return { ...request, options: { ...request.options }, envOverrides };
    default:
      throw new Error("未知命令");
  }
}
