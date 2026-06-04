const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const path = require("node:path");
const { applyUserDataOverride, createDesktopRuntime } = require("./lib/desktopRuntime.cjs");

const projectRoot = path.resolve(__dirname, "..");
applyUserDataOverride(app);

const runtime = createDesktopRuntime({
  app,
  projectRoot,
});
let mainWindow = null;

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

ipcMain.handle("app:get-state", async () => runtime.getAppState());

ipcMain.handle("settings:save-api-key", async (_event, providerId, apiKey) => {
  return runtime.saveProviderApiKey(providerId, apiKey);
});

ipcMain.handle("settings:clear-api-key", async (_event, providerId) => {
  return runtime.clearProviderApiKey(providerId);
});

ipcMain.handle("settings:save-preferences", async (_event, preferences) => {
  return runtime.updatePreferences(preferences);
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

ipcMain.handle("dialog:select-video", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择视频文件",
    properties: ["openFile"],
    filters: [
      { name: "视频文件", extensions: ["mp4", "mkv", "webm", "mov", "m4v", "avi"] },
      { name: "所有文件", extensions: ["*"] },
    ],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("dialog:select-subtitle", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择已翻译字幕",
    properties: ["openFile"],
    filters: [
      { name: "SRT 字幕", extensions: ["srt"] },
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
  return runtime.startJob(event.sender, request);
});

ipcMain.handle("job:cancel", async (_event, jobId) => {
  return runtime.cancelJob(jobId);
});

ipcMain.handle("shell:open-path", async (_event, filePath) => {
  const target = runtime.resolveUserPath(filePath);
  if (!target) {
    return { ok: false, error: "路径为空" };
  }
  const error = await shell.openPath(target);
  return { ok: !error, error };
});

ipcMain.handle("shell:show-in-folder", async (_event, filePath) => {
  const target = runtime.resolveUserPath(filePath);
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
  runtime.stopAllJobs();
  if (process.platform !== "darwin") {
    app.quit();
  }
});
