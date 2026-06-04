const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { buildEnv, buildPythonArgs } = require("./cliCommands.cjs");
const { createFfmpegDetector } = require("./ffmpegStatus.cjs");
const { getProvider } = require("./providerCatalog.cjs");
const { writeDesktopModelConfig } = require("./modelConfig.cjs");
const {
  clearApiKey,
  resolveCredential,
  saveApiKey,
  savePreferences,
  summarizeSettings,
} = require("./settingsStore.cjs");

function applyUserDataOverride(app, env = process.env) {
  if (!env.SUBTITLE_LLM_USER_DATA_DIR) {
    return;
  }
  const userDataDir = path.resolve(env.SUBTITLE_LLM_USER_DATA_DIR);
  fs.mkdirSync(userDataDir, { recursive: true });
  app.setPath("userData", userDataDir);
}

function createDesktopRuntime({
  app,
  projectRoot,
  env = process.env,
  spawnFn = spawn,
  detectFfmpeg = createFfmpegDetector({ env }),
} = {}) {
  const activeJobs = new Map();

  function resolvePythonExecutable() {
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

  function getSettingsPath() {
    return path.join(app.getPath("userData"), "settings.json");
  }

  function getAppState() {
    return {
      projectRoot,
      pythonExecutable: resolvePythonExecutable(),
      hasMainPy: fs.existsSync(path.join(projectRoot, "main.py")),
      settingsPath: getSettingsPath(),
      ffmpeg: detectFfmpeg(),
      ...summarizeSettings(getSettingsPath(), env),
    };
  }

  function saveProviderApiKey(providerId, apiKey) {
    saveApiKey(getSettingsPath(), providerId, apiKey);
    return getAppState();
  }

  function clearProviderApiKey(providerId) {
    clearApiKey(getSettingsPath(), providerId);
    return getAppState();
  }

  function updatePreferences(preferences) {
    savePreferences(getSettingsPath(), preferences);
    return getAppState();
  }

  function startJob(sender, request) {
    if (hasRunningJob()) {
      throw new Error("已有任务正在运行，请先取消或等待完成");
    }

    const mainPy = path.join(projectRoot, "main.py");
    if (!fs.existsSync(mainPy)) {
      throw new Error(`未找到 Python 入口：${mainPy}`);
    }

    const runRequest = prepareRequestForRun(request);
    const args = buildPythonArgs(runRequest);
    const childEnv = buildEnv(env, runRequest.envOverrides);
    const pythonExecutable = resolvePythonExecutable();
    const jobId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const child = spawnFn(pythonExecutable, args, {
      cwd: projectRoot,
      env: childEnv,
      stdio: ["ignore", "pipe", "pipe"],
    });

    const job = {
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

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => sendJobEvent(sender, { type: "stdout", jobId, text: chunk }));
    child.stderr.on("data", (chunk) => sendJobEvent(sender, { type: "stderr", jobId, text: chunk }));
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

  function cancelJob(jobId) {
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

  function stopAllJobs() {
    for (const job of activeJobs.values()) {
      if (!job.finished) {
        job.child.kill("SIGTERM");
      }
    }
  }

  function resolveUserPath(filePath) {
    const cleaned = typeof filePath === "string" ? filePath.trim() : "";
    if (!cleaned) {
      return "";
    }
    return path.isAbsolute(cleaned) ? cleaned : path.resolve(projectRoot, cleaned);
  }

  function hasRunningJob() {
    return Array.from(activeJobs.values()).some((job) => !job.finished);
  }

  function prepareRequestForRun(request) {
    const nextRequest = {
      ...request,
      options: {
        ...(request.options || {}),
      },
      envOverrides: {
        ...(request.envOverrides || {}),
      },
    };

    if (request.command === "translate" && request.modelSelection && request.modelSelection.mode === "service") {
      const provider = getProvider(request.modelSelection.providerId);
      const credential = resolveCredential(getSettingsPath(), provider, env);
      nextRequest.generatedConfigPath = writeDesktopModelConfig(projectRoot, request.modelSelection);
      nextRequest.options.config = nextRequest.generatedConfigPath;
      nextRequest.envOverrides = {
        ...nextRequest.envOverrides,
        ...credential.envOverrides,
      };
    }

    if (["translate", "mux"].includes(request.command)) {
      const ffmpeg = detectFfmpeg();
      if (ffmpeg.available && !nextRequest.options.ffmpeg) {
        nextRequest.options.ffmpeg = ffmpeg.executable;
      }
    }

    return nextRequest;
  }

  function sendJobEvent(sender, payload) {
    if (!sender.isDestroyed()) {
      sender.send("job:event", payload);
    }
  }

  return {
    cancelJob,
    clearProviderApiKey,
    getAppState,
    resolveUserPath,
    saveProviderApiKey,
    startJob,
    stopAllJobs,
    updatePreferences,
  };
}

module.exports = {
  applyUserDataOverride,
  createDesktopRuntime,
};
