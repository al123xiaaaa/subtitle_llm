const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { buildEnv, buildPythonArgs } = require("./lib/cliCommands.cjs");
const { getProvider } = require("./lib/providerCatalog.cjs");
const { writeDesktopModelConfig } = require("./lib/modelConfig.cjs");
const {
  clearApiKey,
  resolveCredential,
  saveApiKey,
  savePreferences,
  summarizeSettings,
} = require("./lib/settingsStore.cjs");

const projectRoot = path.resolve(__dirname, "..");
const activeJobs = new Map();
let mainWindow = null;

function resolvePythonExecutable() {
  if (process.env.SUBTITLE_LLM_PYTHON) {
    return process.env.SUBTITLE_LLM_PYTHON;
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

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 820,
    minWidth: 980,
    minHeight: 680,
    title: "Subtitle LLM",
    backgroundColor: "#f7f7f2",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
}

function sendJobEvent(sender, payload) {
  if (!sender.isDestroyed()) {
    sender.send("job:event", payload);
  }
}

function relativeOrAbsolutePath(filePath) {
  const cleaned = typeof filePath === "string" ? filePath.trim() : "";
  if (!cleaned) {
    return "";
  }
  return path.isAbsolute(cleaned) ? cleaned : path.resolve(projectRoot, cleaned);
}

function hasRunningJob() {
  return Array.from(activeJobs.values()).some((job) => !job.finished);
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
    ...summarizeSettings(getSettingsPath(), process.env),
  };
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
    const credential = resolveCredential(getSettingsPath(), provider, process.env);
    nextRequest.generatedConfigPath = writeDesktopModelConfig(projectRoot, request.modelSelection);
    nextRequest.options.config = nextRequest.generatedConfigPath;
    nextRequest.envOverrides = {
      ...nextRequest.envOverrides,
      ...credential.envOverrides,
    };
  }

  return nextRequest;
}

ipcMain.handle("app:get-state", async () => getAppState());

ipcMain.handle("settings:save-api-key", async (_event, providerId, apiKey) => {
  saveApiKey(getSettingsPath(), providerId, apiKey);
  return getAppState();
});

ipcMain.handle("settings:clear-api-key", async (_event, providerId) => {
  clearApiKey(getSettingsPath(), providerId);
  return getAppState();
});

ipcMain.handle("settings:save-preferences", async (_event, preferences) => {
  savePreferences(getSettingsPath(), preferences);
  return getAppState();
});

ipcMain.handle("dialog:select-input", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择字幕或转写 JSON",
    properties: ["openFile"],
    filters: [
      { name: "字幕与转写文件", extensions: ["srt", "json"] },
      { name: "所有文件", extensions: ["*"] },
    ],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("dialog:select-audio", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择音频文件",
    properties: ["openFile"],
    filters: [
      { name: "音频文件", extensions: ["wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "mp4", "mov"] },
      { name: "所有文件", extensions: ["*"] },
    ],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("dialog:select-config", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择配置 YAML",
    properties: ["openFile"],
    filters: [
      { name: "YAML", extensions: ["yaml", "yml"] },
      { name: "所有文件", extensions: ["*"] },
    ],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("dialog:select-directory", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择文件夹",
    properties: ["openDirectory", "createDirectory"],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("dialog:save-srt", async (_event, defaultName = "output.srt") => {
  const result = await dialog.showSaveDialog(mainWindow, {
    title: "选择输出字幕位置",
    defaultPath: defaultName,
    filters: [
      { name: "SRT 字幕", extensions: ["srt"] },
      { name: "所有文件", extensions: ["*"] },
    ],
  });
  return result.canceled ? null : result.filePath;
});

ipcMain.handle("job:start", async (event, request) => {
  if (hasRunningJob()) {
    throw new Error("已有任务正在运行，请先取消或等待完成");
  }

  const mainPy = path.join(projectRoot, "main.py");
  if (!fs.existsSync(mainPy)) {
    throw new Error(`未找到 Python 入口：${mainPy}`);
  }

  const runRequest = prepareRequestForRun(request);
  const args = buildPythonArgs(runRequest);
  const env = buildEnv(process.env, runRequest.envOverrides);
  const pythonExecutable = resolvePythonExecutable();
  const jobId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const child = spawn(pythonExecutable, args, {
    cwd: projectRoot,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });

  const job = {
    child,
    command: request.command,
    finished: false,
    sender: event.sender,
  };
  activeJobs.set(jobId, job);

  sendJobEvent(event.sender, {
    type: "started",
    jobId,
    command: request.command,
    pythonExecutable,
    cwd: projectRoot,
    generatedConfigPath: runRequest.generatedConfigPath || "",
  });

  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => sendJobEvent(event.sender, { type: "stdout", jobId, text: chunk }));
  child.stderr.on("data", (chunk) => sendJobEvent(event.sender, { type: "stderr", jobId, text: chunk }));
  child.on("error", (error) => {
    job.finished = true;
    activeJobs.delete(jobId);
    sendJobEvent(event.sender, { type: "error", jobId, message: error.message });
  });
  child.on("close", (code, signal) => {
    job.finished = true;
    activeJobs.delete(jobId);
    sendJobEvent(event.sender, { type: "finished", jobId, code, signal });
  });

  return { jobId };
});

ipcMain.handle("job:cancel", async (_event, jobId) => {
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
});

ipcMain.handle("shell:open-path", async (_event, filePath) => {
  const target = relativeOrAbsolutePath(filePath);
  if (!target) {
    return { ok: false, error: "路径为空" };
  }
  const error = await shell.openPath(target);
  return { ok: !error, error };
});

ipcMain.handle("shell:show-in-folder", async (_event, filePath) => {
  const target = relativeOrAbsolutePath(filePath);
  if (!target) {
    return { ok: false, error: "路径为空" };
  }
  shell.showItemInFolder(target);
  return { ok: true };
});

app.whenReady().then(() => {
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  for (const job of activeJobs.values()) {
    if (!job.finished) {
      job.child.kill("SIGTERM");
    }
  }
  if (process.platform !== "darwin") {
    app.quit();
  }
});
