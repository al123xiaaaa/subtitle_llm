const api = window.subtitleLLM;
const RESULT_EVENT_PREFIX = "SUBTITLE_LLM_RESULT ";

const state = {
  app: null,
  activeJobId: null,
  activeCommand: null,
  isBusy: false,
  lastOutputPath: "",
  lastSubtitlePath: "",
  lastEmbeddedVideoPath: "",
  lastSourceVideoPath: "",
  selectedProviderId: "deepseek",
  selectedOnboardingProviderId: "deepseek",
  onboardingDismissed: false,
  embedPreferenceTouched: false,
};

const elements = {
  onboarding: document.querySelector("#onboarding"),
  onboardingProviders: document.querySelector("#onboardingProviders"),
  onboardingApiKey: document.querySelector("#onboardingApiKey"),
  onboardingKeyLabel: document.querySelector("#onboardingKeyLabel"),
  onboardingSave: document.querySelector("#onboardingSave"),
  onboardingSettings: document.querySelector("#onboardingSettings"),
  runtimeInfo: document.querySelector("#runtimeInfo"),
  tabs: Array.from(document.querySelectorAll(".tab")),
  panels: Array.from(document.querySelectorAll(".task-panel")),
  sidebarProviderStatus: document.querySelector("#sidebarProviderStatus"),
  settingsCards: document.querySelector("#settingsCards"),
  providerSelect: document.querySelector("#providerSelect"),
  modelSelect: document.querySelector("#modelSelect"),
  customModelRow: document.querySelector("#customModelRow"),
  customModelInput: document.querySelector("#customModelInput"),
  providerCredentialStatus: document.querySelector("#providerCredentialStatus"),
  configureProvider: document.querySelector("#configureProvider"),
  useYamlConfig: document.querySelector("#useYamlConfig"),
  translateConfig: document.querySelector("#translateConfig"),
  chooseTranslateConfig: document.querySelector("#chooseTranslateConfig"),
  startTranslate: document.querySelector("#startTranslate"),
  embedMkv: document.querySelector("#embedMkv"),
  translateVideo: document.querySelector("#translateVideo"),
  chooseTranslateVideo: document.querySelector("#chooseTranslateVideo"),
  mkvCapabilityStatus: document.querySelector("#mkvCapabilityStatus"),
  muxForm: document.querySelector("#muxForm"),
  muxSubtitle: document.querySelector("#muxSubtitle"),
  muxVideo: document.querySelector("#muxVideo"),
  muxTargetLanguage: document.querySelector("#muxTargetLanguage"),
  startMux: document.querySelector("#startMux"),
  resultFiles: document.querySelector("#resultFiles"),
  subtitleResultRow: document.querySelector("#subtitleResultRow"),
  subtitleResultPath: document.querySelector("#subtitleResultPath"),
  videoResultRow: document.querySelector("#videoResultRow"),
  videoResultPath: document.querySelector("#videoResultPath"),
  logBody: document.querySelector("#logBody"),
  runStatus: document.querySelector("#runStatus"),
  cancelJob: document.querySelector("#cancelJob"),
  clearLog: document.querySelector("#clearLog"),
  openOutput: document.querySelector("#openOutput"),
  showOutput: document.querySelector("#showOutput"),
  translateForm: document.querySelector("#translateForm"),
  downloadForm: document.querySelector("#downloadForm"),
  transcribeForm: document.querySelector("#transcribeForm"),
};

function byId(id) {
  return document.getElementById(id);
}

function value(id) {
  return byId(id).value.trim();
}

function setValue(id, nextValue) {
  if (nextValue) {
    byId(id).value = nextValue;
  }
}

function providers() {
  return state.app?.providers || [];
}

function providerById(providerId) {
  return providers().find((provider) => provider.id === providerId) || providers()[0] || null;
}

function selectedProvider() {
  return providerById(state.selectedProviderId);
}

function selectedModelId(provider) {
  const stored = state.app?.preferences?.modelsByProvider?.[provider.id];
  if (stored && provider.models.some((model) => model.id === stored)) {
    return stored;
  }
  return provider.defaultModel;
}

function selectedCustomModel(provider) {
  return state.app?.preferences?.customModelsByProvider?.[provider.id] || "";
}

function setBusy(isBusy) {
  state.isBusy = isBusy;
  state.activeJobId = isBusy ? state.activeJobId : null;
  elements.cancelJob.disabled = !isBusy;
  document.querySelectorAll("form button, .tabs button, #onboarding button").forEach((button) => {
    if (button.id !== "cancelJob") {
      button.disabled = isBusy;
    }
  });
  renderTranslationAvailability();
  renderMkvAvailability();
}

function appendLog(text, kind = "stdout") {
  const prefix = kind === "stderr" ? "[stderr] " : "";
  elements.logBody.textContent += `${prefix}${text}`;
  elements.logBody.scrollTop = elements.logBody.scrollHeight;
}

function setStatus(text) {
  elements.runStatus.textContent = text;
}

function setOutputPath(filePath) {
  if (!filePath) {
    return;
  }
  state.lastOutputPath = filePath.trim();
  elements.openOutput.disabled = false;
  elements.showOutput.disabled = false;
}

function setSubtitlePath(filePath) {
  if (!filePath) {
    return;
  }
  state.lastSubtitlePath = filePath.trim();
  setOutputPath(state.lastSubtitlePath);
  elements.muxSubtitle.value = state.lastSubtitlePath;
  renderResultFiles();
  renderMkvAvailability();
}

function setSourceVideoPath(filePath) {
  if (!filePath) {
    return;
  }
  state.lastSourceVideoPath = filePath.trim();
  elements.muxVideo.value = state.lastSourceVideoPath;
  renderMkvAvailability();
}

function setEmbeddedVideoPath(filePath) {
  if (!filePath) {
    return;
  }
  state.lastEmbeddedVideoPath = filePath.trim();
  setOutputPath(state.lastEmbeddedVideoPath);
  renderResultFiles();
}

function renderResultFiles() {
  const hasSubtitle = Boolean(state.lastSubtitlePath);
  const hasVideo = Boolean(state.lastEmbeddedVideoPath);
  elements.resultFiles.classList.toggle("is-hidden", !hasSubtitle && !hasVideo);
  elements.subtitleResultRow.classList.toggle("is-hidden", !hasSubtitle);
  elements.videoResultRow.classList.toggle("is-hidden", !hasVideo);
  elements.subtitleResultPath.textContent = state.lastSubtitlePath;
  elements.videoResultPath.textContent = state.lastEmbeddedVideoPath;
}

function parseKnownOutput(text) {
  for (const line of text.split(/\r?\n/)) {
    if (parseResultEvent(line)) {
      continue;
    }
    const outputMatch = line.match(/^输出文件：(.+)$/);
    if (outputMatch) {
      setSubtitlePath(outputMatch[1]);
    }
    const sourceVideoMatch = line.match(/^源视频：(.+)$/);
    if (sourceVideoMatch) {
      setSourceVideoPath(sourceVideoMatch[1]);
    }
    const embeddedVideoMatch = line.match(/^输出视频：(.+)$/);
    if (embeddedVideoMatch) {
      setEmbeddedVideoPath(embeddedVideoMatch[1]);
    }
  }
}

function parseResultEvent(line) {
  if (!line.startsWith(RESULT_EVENT_PREFIX)) {
    return false;
  }

  try {
    const event = JSON.parse(line.slice(RESULT_EVENT_PREFIX.length));
    if (event.output_file) {
      setSubtitlePath(event.output_file);
    }
    if (event.source_video_file) {
      setSourceVideoPath(event.source_video_file);
    }
    if (event.embedded_video_file) {
      setEmbeddedVideoPath(event.embedded_video_file);
    }
    if (event.output_video_file) {
      setEmbeddedVideoPath(event.output_video_file);
    }
    return true;
  } catch (error) {
    appendLog(`结果事件解析失败：${error.message}\n`, "stderr");
    return true;
  }
}

function switchTab(tabName) {
  elements.tabs.forEach((tab) => tab.classList.toggle("is-active", tab.dataset.tab === tabName));
  elements.panels.forEach((panel) => panel.classList.toggle("is-active", panel.dataset.panel === tabName));
}

function renderStatusPill(status) {
  const className = status.available ? `status-pill is-${status.source}` : "status-pill is-missing";
  return `<span class="${className}">${status.label}</span>`;
}

function renderSidebarStatus() {
  elements.sidebarProviderStatus.innerHTML = providers()
    .map(
      (provider) => `
        <div class="provider-status-item">
          <span>${provider.name}</span>
          ${renderStatusPill(provider.credential)}
        </div>
      `,
    )
    .join("");
}

function renderProviderSelect() {
  const previous = state.selectedProviderId || state.app?.preferredProviderId || "deepseek";
  state.selectedProviderId = providerById(previous)?.id || state.app?.preferredProviderId || "deepseek";
  elements.providerSelect.innerHTML = providers()
    .map((provider) => `<option value="${provider.id}">${provider.name}</option>`)
    .join("");
  elements.providerSelect.value = state.selectedProviderId;
}

function renderModelSelect() {
  const provider = selectedProvider();
  if (!provider) {
    return;
  }

  elements.modelSelect.innerHTML = provider.models
    .map((model) => {
      const label = model.description ? `${model.label} - ${model.description}` : model.label;
      return `<option value="${model.id}">${label}</option>`;
    })
    .join("");
  elements.modelSelect.value = selectedModelId(provider);
  elements.customModelInput.value = selectedCustomModel(provider);
  renderCustomModelInput();
}

function renderCustomModelInput() {
  elements.customModelRow.classList.toggle("is-hidden", elements.modelSelect.value !== "__custom__");
}

function renderTranslationAvailability() {
  const provider = selectedProvider();
  const useYaml = elements.useYamlConfig.checked;
  elements.translateConfig.disabled = !useYaml;
  elements.chooseTranslateConfig.disabled = !useYaml;

  if (!provider) {
    elements.providerCredentialStatus.textContent = "未加载";
    elements.providerCredentialStatus.className = "status-pill is-missing";
    elements.startTranslate.disabled = true;
    return;
  }

  const status = provider.credential;
  elements.providerCredentialStatus.textContent = useYaml ? "使用 YAML 配置" : status.label;
  elements.providerCredentialStatus.className = useYaml
    ? "status-pill is-env"
    : `status-pill ${status.available ? `is-${status.source}` : "is-missing"}`;
  elements.configureProvider.textContent = `配置 ${provider.name} API Key`;
  elements.configureProvider.classList.toggle("is-hidden", useYaml || status.available);
  elements.startTranslate.disabled = state.isBusy || (!useYaml && !status.available);
}

function ffmpegAvailable() {
  return Boolean(state.app?.ffmpeg?.available);
}

function looksLikeUrl(rawValue) {
  try {
    const parsed = new URL(rawValue);
    return ["http:", "https:"].includes(parsed.protocol);
  } catch {
    return false;
  }
}

function syncEmbedDefault() {
  if (state.embedPreferenceTouched || !ffmpegAvailable()) {
    return;
  }
  elements.embedMkv.checked = looksLikeUrl(value("translateInput"));
}

function renderMkvAvailability() {
  const available = ffmpegAvailable();
  const statusText = available ? "FFmpeg 可用" : state.app?.ffmpeg?.error || "未找到 FFmpeg";
  elements.mkvCapabilityStatus.textContent = statusText;
  elements.mkvCapabilityStatus.className = available ? "status-pill is-saved" : "status-pill is-missing";

  elements.embedMkv.disabled = state.isBusy || !available;
  elements.translateVideo.disabled = state.isBusy || !available;
  elements.chooseTranslateVideo.disabled = state.isBusy || !available;
  elements.muxSubtitle.disabled = state.isBusy || !available;
  elements.muxVideo.disabled = state.isBusy || !available;
  elements.muxTargetLanguage.disabled = state.isBusy || !available;
  elements.startMux.disabled =
    state.isBusy || !available || !elements.muxSubtitle.value.trim() || !elements.muxVideo.value.trim();
}

function renderSettingsCards() {
  elements.settingsCards.innerHTML = providers()
    .map(
      (provider) => `
        <article class="settings-card" data-provider-card="${provider.id}">
          <div class="settings-card-heading">
            <div>
              <h3>${provider.name}</h3>
              <p>${provider.credential.envKey}</p>
            </div>
            ${renderStatusPill(provider.credential)}
          </div>
          <label>
            <span>${provider.name} API Key</span>
            <input data-api-key-input="${provider.id}" type="password" autocomplete="off" placeholder="粘贴新的 API Key" />
          </label>
          <div class="card-actions">
            <button class="primary-button" type="button" data-save-key="${provider.id}">保存</button>
            <button class="secondary-button" type="button" data-clear-key="${provider.id}">清除</button>
          </div>
        </article>
      `,
    )
    .join("");
}

function renderOnboarding() {
  if (state.app?.hasAnyCredential || state.onboardingDismissed) {
    elements.onboarding.classList.add("is-hidden");
    return;
  }

  elements.onboarding.classList.remove("is-hidden");
  const visibleProviders = providers().filter((provider) => ["deepseek", "gemini", "openai"].includes(provider.id));
  if (!visibleProviders.some((provider) => provider.id === state.selectedOnboardingProviderId)) {
    state.selectedOnboardingProviderId = "deepseek";
  }
  elements.onboardingProviders.innerHTML = visibleProviders
    .map(
      (provider) => `
        <button class="provider-card ${provider.id === state.selectedOnboardingProviderId ? "is-selected" : ""}" type="button" data-onboarding-provider="${provider.id}">
          <strong>${provider.name}</strong>
          <span>${provider.id === "deepseek" ? "推荐" : provider.credential.envKey}</span>
        </button>
      `,
    )
    .join("");
  const selected = providerById(state.selectedOnboardingProviderId);
  elements.onboardingKeyLabel.textContent = selected ? `${selected.name} API Key` : "API Key";
}

function renderAll() {
  if (!state.app) {
    return;
  }
  elements.runtimeInfo.textContent = state.app.hasMainPy ? state.app.pythonExecutable : "未找到 main.py";
  renderSidebarStatus();
  renderProviderSelect();
  renderModelSelect();
  renderSettingsCards();
  renderTranslationAvailability();
  syncEmbedDefault();
  renderMkvAvailability();
  renderOnboarding();
}

function updateAppState(nextState) {
  const previousProviderId = state.selectedProviderId;
  state.app = nextState;
  state.selectedProviderId =
    previousProviderId || nextState.preferences?.lastProviderId || nextState.preferredProviderId || "deepseek";
  renderAll();
}

async function persistProviderPreference() {
  const provider = selectedProvider();
  if (!provider) {
    return;
  }

  const preferences = {
    ...state.app.preferences,
    lastProviderId: provider.id,
    modelsByProvider: {
      ...(state.app.preferences?.modelsByProvider || {}),
      [provider.id]: elements.modelSelect.value,
    },
    customModelsByProvider: {
      ...(state.app.preferences?.customModelsByProvider || {}),
      [provider.id]: elements.customModelInput.value.trim(),
    },
  };
  updateAppState(await api.savePreferences(preferences));
}

function modelSelection() {
  const provider = selectedProvider();
  return {
    mode: "service",
    providerId: provider.id,
    modelId: elements.modelSelect.value,
    customModelId: elements.customModelInput.value.trim(),
  };
}

async function startJob(command, options, selection = null) {
  try {
    setBusy(true);
    setStatus("启动中");
    appendLog(`\n$ subtitle-llm ${command}\n`);
    const response = await api.startJob({
      command,
      options,
      modelSelection: selection,
    });
    state.activeJobId = response.jobId;
    state.activeCommand = command;
  } catch (error) {
    setBusy(false);
    setStatus("启动失败");
    appendLog(`${error.message}\n`, "stderr");
  }
}

elements.tabs.forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});

elements.providerSelect.addEventListener("change", async () => {
  state.selectedProviderId = elements.providerSelect.value;
  renderModelSelect();
  renderTranslationAvailability();
  await persistProviderPreference();
});

elements.modelSelect.addEventListener("change", async () => {
  renderCustomModelInput();
  await persistProviderPreference();
});

elements.customModelInput.addEventListener("change", persistProviderPreference);
elements.useYamlConfig.addEventListener("change", renderTranslationAvailability);
elements.embedMkv.addEventListener("change", () => {
  state.embedPreferenceTouched = true;
  renderMkvAvailability();
});
byId("translateInput").addEventListener("input", () => {
  syncEmbedDefault();
  renderMkvAvailability();
});
byId("targetLanguage").addEventListener("input", () => {
  elements.muxTargetLanguage.value = value("targetLanguage") || "Chinese";
});
elements.muxSubtitle.addEventListener("input", renderMkvAvailability);
elements.muxVideo.addEventListener("input", renderMkvAvailability);
elements.translateVideo.addEventListener("input", () => {
  if (value("translateVideo")) {
    elements.muxVideo.value = value("translateVideo");
  }
  renderMkvAvailability();
});

elements.configureProvider.addEventListener("click", () => {
  switchTab("settings");
  document.querySelector(`[data-provider-card="${state.selectedProviderId}"]`)?.scrollIntoView({ block: "center" });
});

elements.settingsCards.addEventListener("click", async (event) => {
  const saveProviderId = event.target.dataset.saveKey;
  const clearProviderId = event.target.dataset.clearKey;
  if (saveProviderId) {
    try {
      const input = document.querySelector(`[data-api-key-input="${saveProviderId}"]`);
      updateAppState(await api.saveApiKey(saveProviderId, input.value));
      input.value = "";
    } catch (error) {
      appendLog(`${error.message}\n`, "stderr");
    }
  }
  if (clearProviderId) {
    try {
      updateAppState(await api.clearApiKey(clearProviderId));
    } catch (error) {
      appendLog(`${error.message}\n`, "stderr");
    }
  }
});

elements.onboardingProviders.addEventListener("click", (event) => {
  const providerId = event.target.closest("[data-onboarding-provider]")?.dataset.onboardingProvider;
  if (!providerId) {
    return;
  }
  state.selectedOnboardingProviderId = providerId;
  renderOnboarding();
});

elements.onboardingSave.addEventListener("click", async () => {
  try {
    updateAppState(await api.saveApiKey(state.selectedOnboardingProviderId, elements.onboardingApiKey.value));
    state.selectedProviderId = state.selectedOnboardingProviderId;
    elements.onboardingApiKey.value = "";
    elements.onboarding.classList.add("is-hidden");
    renderAll();
  } catch (error) {
    appendLog(`${error.message}\n`, "stderr");
  }
});

elements.onboardingSettings.addEventListener("click", () => {
  state.onboardingDismissed = true;
  elements.onboarding.classList.add("is-hidden");
  switchTab("settings");
});

byId("chooseInput").addEventListener("click", async () => {
  setValue("translateInput", await api.selectInput());
  syncEmbedDefault();
  renderMkvAvailability();
});
byId("chooseTranslateVideo").addEventListener("click", async () => {
  setValue("translateVideo", await api.selectVideo());
  if (value("translateVideo")) {
    elements.embedMkv.checked = true;
    state.embedPreferenceTouched = true;
    elements.muxVideo.value = value("translateVideo");
  }
  renderMkvAvailability();
});
byId("chooseTranslateConfig").addEventListener("click", async () => setValue("translateConfig", await api.selectConfig()));
byId("chooseTranscribeConfig").addEventListener("click", async () => setValue("transcribeConfig", await api.selectConfig()));
byId("chooseDownloadDir").addEventListener("click", async () => setValue("downloadOutputDir", await api.selectDirectory()));
byId("chooseMuxSubtitle").addEventListener("click", async () => {
  setValue("muxSubtitle", await api.selectSubtitle());
  renderMkvAvailability();
});
byId("chooseMuxVideo").addEventListener("click", async () => {
  setValue("muxVideo", await api.selectVideo());
  renderMkvAvailability();
});
byId("chooseAudio").addEventListener("click", async () => {
  const audio = await api.selectAudio();
  setValue("audioInput", audio);
  if (audio && !value("transcribeOutput")) {
    setValue("transcribeOutput", audio.replace(/\.[^.]+$/, ".srt"));
  }
});
byId("chooseOutput").addEventListener("click", async () => {
  const defaultName = value("translateInput").replace(/\.[^.]+$/, ".zh.srt") || "output.zh.srt";
  setValue("translateOutput", await api.saveSrt(defaultName));
});
byId("chooseTranscribeOutput").addEventListener("click", async () => {
  const defaultName = value("audioInput").replace(/\.[^.]+$/, ".srt") || "transcript.srt";
  setValue("transcribeOutput", await api.saveSrt(defaultName));
});

elements.translateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const output = value("translateOutput");
  if (output) {
    setSubtitlePath(output);
  }
  await persistProviderPreference();
  const input = value("translateInput");
  const video = value("translateVideo");
  const embedVideo = elements.embedMkv.checked && ffmpegAvailable() && (looksLikeUrl(input) || video);
  await startJob(
    "translate",
    {
      input,
      targetLanguage: value("targetLanguage"),
      sourceLanguage: value("sourceLanguage"),
      output,
      config: elements.useYamlConfig.checked ? value("translateConfig") : "",
      outputFormat: value("outputFormat"),
      reviewMode: value("reviewMode"),
      resume: byId("resumeTranslate").checked,
      embedVideo,
      video,
    },
    elements.useYamlConfig.checked ? null : modelSelection(),
  );
});

elements.muxForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await startJob("mux", {
    subtitle: value("muxSubtitle"),
    video: value("muxVideo"),
    targetLanguage: value("muxTargetLanguage") || value("targetLanguage") || "Chinese",
  });
});

elements.downloadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await startJob("download", {
    url: value("downloadUrl"),
    outputDir: value("downloadOutputDir"),
    sourceLanguage: value("downloadSourceLanguage"),
  });
});

elements.transcribeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const output = value("transcribeOutput");
  setOutputPath(output);
  await startJob("transcribe", {
    audio: value("audioInput"),
    output,
    language: value("transcribeLanguage"),
    config: value("transcribeConfig"),
  });
});

elements.cancelJob.addEventListener("click", async () => {
  if (!state.activeJobId) {
    return;
  }
  const result = await api.cancelJob(state.activeJobId);
  if (result.ok) {
    setStatus("正在取消");
    appendLog("正在取消任务...\n");
  }
});

elements.clearLog.addEventListener("click", () => {
  elements.logBody.textContent = "";
});

elements.resultFiles.addEventListener("click", async (event) => {
  const openTarget = event.target.dataset.openResult;
  const showTarget = event.target.dataset.showResult;
  const target = openTarget || showTarget;
  if (!target) {
    return;
  }
  const filePath = target === "video" ? state.lastEmbeddedVideoPath : state.lastSubtitlePath;
  if (!filePath) {
    return;
  }
  if (openTarget) {
    await api.openPath(filePath);
  } else {
    await api.showInFolder(filePath);
  }
});

elements.openOutput.addEventListener("click", async () => {
  if (state.lastOutputPath) {
    await api.openPath(state.lastOutputPath);
  }
});

elements.showOutput.addEventListener("click", async () => {
  if (state.lastOutputPath) {
    await api.showInFolder(state.lastOutputPath);
  }
});

api.onJobEvent((event) => {
  if (event.type === "started") {
    setStatus("运行中");
    appendLog(`Python: ${event.pythonExecutable}\n工作目录: ${event.cwd}\n`);
    if (event.generatedConfigPath) {
      appendLog(`模型配置: ${event.generatedConfigPath}\n`);
    }
  } else if (event.type === "stdout") {
    appendLog(event.text);
    parseKnownOutput(event.text);
  } else if (event.type === "stderr") {
    appendLog(event.text, "stderr");
  } else if (event.type === "error") {
    setBusy(false);
    setStatus("失败");
    appendLog(`${event.message}\n`, "stderr");
  } else if (event.type === "finished") {
    setBusy(false);
    setStatus(event.code === 0 ? "完成" : `退出码 ${event.code}`);
    appendLog(`\n任务结束：code=${event.code} signal=${event.signal || "none"}\n`, event.code === 0 ? "stdout" : "stderr");
  }
});

api.getState().then(updateAppState).catch((error) => {
  setStatus("加载失败");
  appendLog(`${error.message}\n`, "stderr");
});
