const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("subtitleLLM", {
  getState: () => ipcRenderer.invoke("app:get-state"),
  saveApiKey: (providerId, apiKey) => ipcRenderer.invoke("settings:save-api-key", providerId, apiKey),
  clearApiKey: (providerId) => ipcRenderer.invoke("settings:clear-api-key", providerId),
  savePreferences: (preferences) => ipcRenderer.invoke("settings:save-preferences", preferences),
  selectInput: () => ipcRenderer.invoke("dialog:select-input"),
  selectAudio: () => ipcRenderer.invoke("dialog:select-audio"),
  selectConfig: () => ipcRenderer.invoke("dialog:select-config"),
  selectDirectory: () => ipcRenderer.invoke("dialog:select-directory"),
  saveSrt: (defaultName) => ipcRenderer.invoke("dialog:save-srt", defaultName),
  startJob: (request) => ipcRenderer.invoke("job:start", request),
  cancelJob: (jobId) => ipcRenderer.invoke("job:cancel", jobId),
  openPath: (filePath) => ipcRenderer.invoke("shell:open-path", filePath),
  showInFolder: (filePath) => ipcRenderer.invoke("shell:show-in-folder", filePath),
  onJobEvent: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("job:event", listener);
    return () => ipcRenderer.removeListener("job:event", listener);
  },
});
