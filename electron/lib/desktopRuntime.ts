import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import type { App, WebContents } from "electron";
import type {
  AppState,
  DesktopJobRequest,
  DesktopPreferences,
  FfmpegStatus,
  JobEvent,
  TranslationTaskSummary,
} from "../types.js";
import { buildEnv, buildPythonArgs } from "./cliCommands.js";
import { prepareDesktopTaskIntent } from "./desktopTaskIntent.js";
import { createFfmpegDetector } from "./ffmpegStatus.js";
import { getProvider, modelsFromApiResponse } from "./providerCatalog.js";
import { clearApiKey, resolveCredential, saveApiKey, savePreferences, summarizeSettings } from "./settingsStore.js";

interface DesktopRuntimeOptions {
  app: Pick<App, "getPath">;
  projectRoot: string;
  env?: NodeJS.ProcessEnv;
  spawnFn?: typeof spawn;
  spawnSyncFn?: typeof spawnSync;
  detectFfmpeg?: () => FfmpegStatus;
}

interface PythonModuleRequirement {
  moduleName: string;
  packageName: string;
  installExtra?: string;
}

const BASE_PYTHON_MODULES: PythonModuleRequirement[] = [
  { moduleName: "pysubs2", packageName: "pysubs2" },
  { moduleName: "pysbd", packageName: "pysbd" },
];

const TRANSCRIBE_PYTHON_MODULES: PythonModuleRequirement[] = [
  { moduleName: "funasr", packageName: "funasr", installExtra: ".[asr]" },
];

function cleanText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

interface ActiveJob {
  child: ReturnType<typeof spawn>;
  command: DesktopJobRequest["command"];
  finished: boolean;
  sender: Pick<WebContents, "isDestroyed" | "send">;
}

export function applyUserDataOverride(app: Pick<App, "setPath">, env: NodeJS.ProcessEnv = process.env): void {
  if (!env.SUBTITLE_LLM_USER_DATA_DIR) {
    return;
  }
  const userDataDir = path.resolve(env.SUBTITLE_LLM_USER_DATA_DIR);
  fs.mkdirSync(userDataDir, { recursive: true });
  app.setPath("userData", userDataDir);
}

export function createDesktopRuntime({
  app,
  projectRoot,
  env = process.env,
  spawnFn = spawn,
  spawnSyncFn = spawnSync,
  detectFfmpeg = createFfmpegDetector({ env }),
}: DesktopRuntimeOptions) {
  const activeJobs = new Map<string, ActiveJob>();

  function resolvePythonExecutable(): string {
    if (env.SUBTITLE_LLM_PYTHON) {
      return env.SUBTITLE_LLM_PYTHON;
    }

    const unixVenvPython = path.join(projectRoot, ".venv", "bin", "python");
    if (fs.existsSync(unixVenvPython)) {
      return unixVenvPython;
    }

    const windowsVenvPython = path.join(projectRoot, ".venv", "Scripts", "python.exe");
    if (fs.existsSync(windowsVenvPython)) {
      return windowsVenvPython;
    }

    return process.platform === "win32" ? "python" : "python3";
  }

  function getSettingsPath(): string {
    return path.join(app.getPath("userData"), "settings.json");
  }

  function getAppState(): AppState {
    return {
      projectRoot,
      pythonExecutable: resolvePythonExecutable(),
      hasMainPy: fs.existsSync(path.join(projectRoot, "main.py")),
      settingsPath: getSettingsPath(),
      ffmpeg: detectFfmpeg(),
      ...summarizeSettings(getSettingsPath(), env),
    };
  }

  function saveProviderApiKey(providerId: string, apiKey: string): AppState {
    saveApiKey(getSettingsPath(), providerId, apiKey);
    return getAppState();
  }

  function clearProviderApiKey(providerId: string): AppState {
    clearApiKey(getSettingsPath(), providerId);
    return getAppState();
  }

  function updatePreferences(preferences: DesktopPreferences): AppState {
    savePreferences(getSettingsPath(), preferences);
    return getAppState();
  }

  // 动态拉取服务的模型列表（如本地 CLIProxyAPI 的 /v1/models）。
  // 拉取失败返回空数组，渲染端回退到静态兜底列表。
  async function listProviderModels(providerId: string) {
    const provider = getProvider(providerId);
    if (!provider.dynamicModels || !provider.endpoint) {
      return [];
    }

    let apiKey = cleanText(env[provider.envKey]);
    try {
      const resolved = resolveCredential(getSettingsPath(), provider, env);
      apiKey = cleanText(resolved.envOverrides[provider.envKey]) || apiKey;
    } catch {
      // 未配置 Key 时仍尝试无鉴权拉取（本地服务可能不校验）
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(`${provider.endpoint}/models`, {
        headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
        signal: controller.signal,
      });
      if (!response.ok) {
        return [];
      }
      return modelsFromApiResponse(await response.json());
    } catch {
      return [];
    } finally {
      clearTimeout(timer);
    }
  }

  function startJob(sender: Pick<WebContents, "isDestroyed" | "send">, request: DesktopJobRequest) {
    if (hasRunningJob()) {
      throw new Error("已有任务正在运行，请先取消或等待完成");
    }

    const mainPy = path.join(projectRoot, "main.py");
    if (!fs.existsSync(mainPy)) {
      throw new Error(`未找到 Python 入口：${mainPy}`);
    }

    const pythonExecutable = resolvePythonExecutable();
    assertPythonEnvironmentReady(pythonExecutable, request.command);
    const runRequest = prepareDesktopTaskIntent(request, {
      projectRoot,
      settingsPath: getSettingsPath(),
      env,
      detectFfmpeg,
    });
    const args = buildPythonArgs(runRequest);
    // yt-dlp 合并音视频流需要 ffmpeg；Finder 启动时 PATH 不含 Homebrew，
    // 把桌面端探测到的 ffmpeg 路径显式传给 Python 下载器（见 downloader.py）
    const ffmpegStatus = detectFfmpeg();
    const childEnv = withUserDataEnv(
      buildEnv(env, {
        ...runRequest.envOverrides,
        ...(ffmpegStatus.available ? { SUBTITLE_LLM_FFMPEG: ffmpegStatus.executable } : {}),
      }),
    );
    const jobId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const child = spawnFn(pythonExecutable, args, {
      cwd: projectRoot,
      env: childEnv,
      stdio: ["ignore", "pipe", "pipe"],
    });

    const job: ActiveJob = {
      child,
      command: request.command,
      finished: false,
      sender,
    };
    activeJobs.set(jobId, job);

    sendJobEvent(sender, {
      type: "started",
      jobId,
      command: request.command,
      pythonExecutable,
      cwd: projectRoot,
      generatedConfigPath: runRequest.generatedConfigPath || "",
    });

    child.stdout?.setEncoding("utf8");
    child.stderr?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => sendJobEvent(sender, { type: "stdout", jobId, text: chunk }));
    child.stderr?.on("data", (chunk: string) => sendJobEvent(sender, { type: "stderr", jobId, text: chunk }));
    child.on("error", (error) => {
      job.finished = true;
      activeJobs.delete(jobId);
      sendJobEvent(sender, { type: "error", jobId, message: error.message });
    });
    child.on("close", (code, signal) => {
      job.finished = true;
      activeJobs.delete(jobId);
      sendJobEvent(sender, { type: "finished", jobId, code, signal });
    });

    return { jobId };
  }

  function cancelJob(jobId: string) {
    const job = activeJobs.get(jobId);
    if (!job) {
      return { ok: false, message: "任务不存在或已结束" };
    }
    job.child.kill("SIGTERM");
    setTimeout(() => {
      if (!job.finished) {
        job.child.kill("SIGKILL");
      }
    }, 2500);
    return { ok: true };
  }

  function stopAllJobs(): void {
    for (const job of activeJobs.values()) {
      if (!job.finished) {
        job.child.kill("SIGTERM");
      }
    }
  }

  function resolveUserPath(filePath: unknown): string {
    const cleaned = typeof filePath === "string" ? filePath.trim() : "";
    if (!cleaned) {
      return "";
    }
    return path.isAbsolute(cleaned) ? cleaned : path.resolve(projectRoot, cleaned);
  }

  function hasRunningJob(): boolean {
    return Array.from(activeJobs.values()).some((job) => !job.finished);
  }

  function withUserDataEnv(baseEnv: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
    return {
      ...baseEnv,
      SUBTITLE_LLM_USER_DATA_DIR: app.getPath("userData"),
    };
  }

  function runTasksCommand(args: string[]) {
    const result = spawnSyncFn(resolvePythonExecutable(), ["main.py", "tasks", ...args], {
      cwd: projectRoot,
      env: withUserDataEnv(buildEnv(env, {})),
      encoding: "utf8",
    });
    if (result.error) {
      throw result.error;
    }
    if (result.status !== 0) {
      throw new Error(result.stderr || result.stdout || `任务记录命令失败：${result.status}`);
    }
    return result.stdout || "";
  }

  function listTranslationTasks(includeDeleted = false): TranslationTaskSummary[] {
    const stdout = runTasksCommand(["--json", ...(includeDeleted ? ["--include-deleted"] : [])]);
    return JSON.parse(stdout || "[]") as TranslationTaskSummary[];
  }

  function softDeleteTranslationTask(taskId: string) {
    const cleaned = cleanText(taskId);
    if (!cleaned) {
      return { ok: false, error: "任务记录为空" };
    }
    runTasksCommand(["--delete", cleaned]);
    return { ok: true };
  }

  function restoreTranslationTask(taskId: string) {
    const cleaned = cleanText(taskId);
    if (!cleaned) {
      return { ok: false, error: "任务记录为空" };
    }
    runTasksCommand(["--restore", cleaned]);
    return { ok: true };
  }

  function assertPythonEnvironmentReady(pythonExecutable: string, command: DesktopJobRequest["command"]): void {
    const requirements = pythonRequirementsForCommand(command);
    if (requirements.length === 0) {
      return;
    }

    const moduleNames = requirements.map((requirement) => requirement.moduleName);
    const checkScript = [
      "import importlib.util, json, sys",
      `modules = ${JSON.stringify(moduleNames)}`,
      "missing = [name for name in modules if importlib.util.find_spec(name) is None]",
      "print(json.dumps(missing, ensure_ascii=False))",
      "sys.exit(1 if missing else 0)",
    ].join("\n");
    const result = spawnSyncFn(pythonExecutable, ["-c", checkScript], {
      cwd: projectRoot,
      env: buildEnv(env),
      encoding: "utf8",
    });

    if (result.error) {
      throw new Error(`无法运行 Python：${result.error.message}\n当前 GUI 使用的 Python：${pythonExecutable}`);
    }
    if (result.status === 0) {
      return;
    }

    const missingModules = parseMissingPythonModules(result.stdout);
    if (missingModules.length > 0) {
      throw new Error(buildMissingDependencyMessage(pythonExecutable, requirements, missingModules));
    }

    const stderr = cleanText(result.stderr);
    throw new Error(
      [
        "Python 环境检查失败，请先确认本地环境可用。",
        `当前 GUI 使用的 Python：${pythonExecutable}`,
        stderr ? `错误信息：${stderr}` : "",
      ].filter(Boolean).join("\n"),
    );
  }

  function pythonRequirementsForCommand(command: DesktopJobRequest["command"]): PythonModuleRequirement[] {
    if (command === "transcribe") {
      return [...BASE_PYTHON_MODULES, ...TRANSCRIBE_PYTHON_MODULES];
    }
    return BASE_PYTHON_MODULES;
  }

  function parseMissingPythonModules(stdout: string | Buffer | null | undefined): string[] {
    const text = String(stdout || "").trim();
    if (!text) {
      return [];
    }
    try {
      const parsed = JSON.parse(text);
      return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === "string") : [];
    } catch {
      return [];
    }
  }

  function buildMissingDependencyMessage(
    pythonExecutable: string,
    requirements: PythonModuleRequirement[],
    missingModules: string[],
  ): string {
    const missingPackages = requirements
      .filter((requirement) => missingModules.includes(requirement.moduleName))
      .map((requirement) => requirement.packageName);
    const hasOptionalExtra = requirements.some(
      (requirement) => missingModules.includes(requirement.moduleName) && requirement.installExtra,
    );
    const installTarget = hasOptionalExtra ? ".[asr]" : ".";
    return [
      `Python 环境缺少依赖：${missingPackages.join(", ") || missingModules.join(", ")}`,
      `请在项目目录运行：${pythonExecutable} -m pip install -e "${installTarget}"`,
      `当前 GUI 使用的 Python：${pythonExecutable}`,
    ].join("\n");
  }

  function sendJobEvent(sender: Pick<WebContents, "isDestroyed" | "send">, payload: JobEvent): void {
    if (!sender.isDestroyed()) {
      sender.send("job:event", payload);
    }
  }

  return {
    cancelJob,
    clearProviderApiKey,
    getAppState,
    listProviderModels,
    listTranslationTasks,
    resolveUserPath,
    restoreTranslationTask,
    saveProviderApiKey,
    softDeleteTranslationTask,
    startJob,
    stopAllJobs,
    updatePreferences,
  };
}
