import { contextBridge, ipcRenderer } from "electron";
import type { DesktopJobRequest, DesktopPreferences, JobEvent, SubtitleLlmBridge } from "./types.js";

const bridge: SubtitleLlmBridge = {
  getState: () => ipcRenderer.invoke("app:get-state"),
  saveApiKey: (providerId: string, apiKey: string) => ipcRenderer.invoke("settings:save-api-key", providerId, apiKey),
  clearApiKey: (providerId: string) => ipcRenderer.invoke("settings:clear-api-key", providerId),
  savePreferences: (preferences: DesktopPreferences) => ipcRenderer.invoke("settings:save-preferences", preferences),
  selectInput: () => ipcRenderer.invoke("dialog:select-input"),
  selectAudio: () => ipcRenderer.invoke("dialog:select-audio"),
  selectVideo: () => ipcRenderer.invoke("dialog:select-video"),
  selectSubtitle: () => ipcRenderer.invoke("dialog:select-subtitle"),
  selectConfig: () => ipcRenderer.invoke("dialog:select-config"),
  selectDirectory: () => ipcRenderer.invoke("dialog:select-directory"),
  saveSrt: (defaultName?: string) => ipcRenderer.invoke("dialog:save-srt", defaultName),
  startJob: (request: DesktopJobRequest) => ipcRenderer.invoke("job:start", request),
  cancelJob: (jobId: string) => ipcRenderer.invoke("job:cancel", jobId),
  openPath: (filePath: string) => ipcRenderer.invoke("shell:open-path", filePath),
  showInFolder: (filePath: string) => ipcRenderer.invoke("shell:show-in-folder", filePath),
  onJobEvent: (callback: (event: JobEvent) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, payload: JobEvent) => callback(payload);
    ipcRenderer.on("job:event", listener);
    return () => ipcRenderer.removeListener("job:event", listener);
  },
};

contextBridge.exposeInMainWorld("subtitleLLM", bridge);
