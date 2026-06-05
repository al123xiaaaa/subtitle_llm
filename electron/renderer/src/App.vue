<script setup lang="ts">
import { useAppController } from "./composables/useAppController";

const {
  activeTab,
  apiKeyDrafts,
  canStartMux,
  canStartTranslate,
  cancelJob,
  chooseAudio,
  chooseDownloadDir,
  chooseInput,
  chooseMuxSubtitle,
  chooseMuxVideo,
  chooseOutput,
  chooseTranscribeConfig,
  chooseTranscribeOutput,
  chooseTranslateConfig,
  chooseTranslateVideo,
  clearLog,
  clearSettingsKey,
  configureProvider,
  configureProviderText,
  customModelInput,
  downloadForm,
  ffmpegAvailable,
  hasAnyResult,
  hasSubtitleResult,
  hasVideoResult,
  isBusy,
  lastEmbeddedVideoPath,
  lastOutputPath,
  lastSubtitlePath,
  logBody,
  logText,
  mkvCapabilityClass,
  mkvCapabilityText,
  muxForm,
  onboardingApiKey,
  onboardingKeyLabel,
  onEmbedMkvChanged,
  onModelChanged,
  onProviderChanged,
  onTranslateInput,
  openOutput,
  openResult,
  openSettingsFromOnboarding,
  persistProviderPreference,
  providerCredentialStatus,
  providerModels,
  providers,
  runStatus,
  runtimeInfo,
  saveOnboardingKey,
  saveSettingsKey,
  selectedModelId,
  selectedOnboardingProviderId,
  selectedProviderId,
  selectOnboardingProvider,
  setActiveTab,
  showConfigureProvider,
  showCustomModelInput,
  showOnboarding,
  showOutput,
  showResult,
  statusPillClass,
  submitDownload,
  submitMux,
  submitTranscribe,
  submitTranslate,
  taskTabs,
  transcribeForm,
  translateForm,
  visibleOnboardingProviders,
} = useAppController();
</script>

<template>
  <div
    id="onboarding"
    :class="['onboarding', { 'is-hidden': !showOnboarding }]"
    aria-modal="true"
    role="dialog"
  >
    <section class="onboarding-panel">
      <p class="eyebrow">
        首次配置
      </p>
      <h1>先连接一个翻译服务</h1>
      <p>保存 API Key 后就可以开始翻译字幕。也可以使用系统环境变量，App 会自动识别。</p>
      <div
        id="onboardingProviders"
        class="provider-cards"
      >
        <button
          v-for="provider in visibleOnboardingProviders"
          :key="provider.id"
          :class="['provider-card', { 'is-selected': provider.id === selectedOnboardingProviderId }]"
          type="button"
          :data-onboarding-provider="provider.id"
          :disabled="isBusy"
          @click="selectOnboardingProvider(provider.id)"
        >
          <strong>{{ provider.name }}</strong>
          <span>{{ provider.id === "deepseek" ? "推荐" : provider.credential.envKey }}</span>
        </button>
      </div>
      <label>
        <span id="onboardingKeyLabel">{{ onboardingKeyLabel }}</span>
        <input
          id="onboardingApiKey"
          v-model="onboardingApiKey"
          type="password"
          autocomplete="off"
          :disabled="isBusy"
        >
      </label>
      <div class="actions">
        <button
          id="onboardingSave"
          class="primary-button"
          type="button"
          :disabled="isBusy"
          @click="saveOnboardingKey"
        >
          保存并开始
        </button>
        <button
          id="onboardingSettings"
          class="secondary-button"
          type="button"
          :disabled="isBusy"
          @click="openSettingsFromOnboarding"
        >
          打开设置
        </button>
      </div>
    </section>
  </div>

  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">
          SL
        </div>
        <div>
          <h1>Subtitle LLM</h1>
          <p id="runtimeInfo">
            {{ runtimeInfo }}
          </p>
        </div>
      </div>
      <nav
        class="tabs"
        aria-label="任务类型"
      >
        <button
          v-for="tab in taskTabs"
          :key="tab.id"
          :class="['tab', { 'is-active': activeTab === tab.id }]"
          type="button"
          :data-tab="tab.id"
          :disabled="isBusy"
          @click="setActiveTab(tab.id)"
        >
          {{ tab.label }}
        </button>
      </nav>
      <section
        class="status-panel"
        aria-labelledby="statusTitle"
      >
        <h2 id="statusTitle">
          服务状态
        </h2>
        <div
          id="sidebarProviderStatus"
          class="provider-status-list"
        >
          <div
            v-for="provider in providers"
            :key="provider.id"
            class="provider-status-item"
          >
            <span>{{ provider.name }}</span>
            <span :class="statusPillClass(provider.credential)">{{ provider.credential.label }}</span>
          </div>
        </div>
      </section>
    </aside>

    <main class="workspace">
      <section
        :class="['task-panel', { 'is-active': activeTab === 'translate' }]"
        data-panel="translate"
        aria-labelledby="translateTitle"
      >
        <div class="panel-heading">
          <div>
            <h2 id="translateTitle">
              翻译字幕
            </h2>
            <p>SRT、转写 JSON 或视频 URL</p>
          </div>
          <button
            id="chooseInput"
            class="secondary-button"
            type="button"
            :disabled="isBusy"
            @click="chooseInput"
          >
            选择文件
          </button>
        </div>
        <form
          id="translateForm"
          class="form-grid"
          @submit.prevent="submitTranslate"
        >
          <label>
            <span>翻译服务</span>
            <select
              id="providerSelect"
              v-model="selectedProviderId"
              :disabled="isBusy"
              @change="onProviderChanged"
            >
              <option
                v-for="provider in providers"
                :key="provider.id"
                :value="provider.id"
              >{{ provider.name }}</option>
            </select>
          </label>
          <label>
            <span>模型</span>
            <select
              id="modelSelect"
              v-model="selectedModelId"
              :disabled="isBusy"
              @change="onModelChanged"
            >
              <option
                v-for="model in providerModels"
                :key="model.id"
                :value="model.id"
              >
                {{ model.description ? `${model.label} - ${model.description}` : model.label }}
              </option>
            </select>
          </label>
          <label
            id="customModelRow"
            :class="['span-2', { 'is-hidden': !showCustomModelInput }]"
          >
            <span>自定义模型 ID</span>
            <input
              id="customModelInput"
              v-model="customModelInput"
              type="text"
              autocomplete="off"
              :disabled="isBusy"
              @change="persistProviderPreference"
            >
          </label>
          <div class="span-2 credential-row">
            <span
              id="providerCredentialStatus"
              :class="providerCredentialStatus.className"
            >{{ providerCredentialStatus.text }}</span>
            <button
              id="configureProvider"
              :class="['secondary-button', { 'is-hidden': !showConfigureProvider }]"
              type="button"
              :disabled="isBusy"
              @click="configureProvider"
            >
              {{ configureProviderText }}
            </button>
          </div>
          <label class="span-2">
            <span>输入</span>
            <input
              id="translateInput"
              v-model="translateForm.input"
              name="input"
              type="text"
              placeholder="input.srt / input.json / https://..."
              required
              :disabled="isBusy"
              @input="onTranslateInput"
            >
          </label>
          <label>
            <span>目标语言</span>
            <input
              id="targetLanguage"
              v-model="translateForm.targetLanguage"
              name="targetLanguage"
              type="text"
              required
              :disabled="isBusy"
            >
          </label>
          <label>
            <span>源语言</span>
            <input
              id="sourceLanguage"
              v-model="translateForm.sourceLanguage"
              name="sourceLanguage"
              type="text"
              :disabled="isBusy"
            >
          </label>
          <label>
            <span>输出格式</span>
            <select
              id="outputFormat"
              v-model="translateForm.outputFormat"
              name="outputFormat"
              :disabled="isBusy"
            >
              <option value="">使用配置默认值</option>
              <option value="source-first">原文在前</option>
              <option value="target-first">译文在前</option>
              <option value="bilingual">双语</option>
              <option value="target-only">仅译文</option>
              <option value="source-only">仅原文</option>
            </select>
          </label>
          <label>
            <span>复核方式</span>
            <select
              id="reviewMode"
              v-model="translateForm.reviewMode"
              name="reviewMode"
              :disabled="isBusy"
            >
              <option value="auto">自动修复</option>
              <option value="config">使用配置</option>
              <option value="tui">TUI 复核</option>
            </select>
          </label>
          <label class="span-2">
            <span>输出文件</span>
            <div class="inline-control">
              <input
                id="translateOutput"
                v-model="translateForm.output"
                name="output"
                type="text"
                placeholder="默认写入 data/output"
                :disabled="isBusy"
              >
              <button
                id="chooseOutput"
                class="secondary-button"
                type="button"
                :disabled="isBusy"
                @click="chooseOutput"
              >选择</button>
            </div>
          </label>
          <section
            class="mkv-panel span-2"
            aria-labelledby="embedMkvTitle"
          >
            <div class="mkv-panel-heading">
              <label class="check-row">
                <input
                  id="embedMkv"
                  v-model="translateForm.embedMkv"
                  type="checkbox"
                  :disabled="isBusy || !ffmpegAvailable"
                  @change="onEmbedMkvChanged"
                >
                <span id="embedMkvTitle">翻译完成后生成带字幕 MKV</span>
              </label>
              <span
                id="mkvCapabilityStatus"
                :class="mkvCapabilityClass"
              >{{ mkvCapabilityText }}</span>
            </div>
            <label>
              <span>视频文件</span>
              <div class="inline-control">
                <input
                  id="translateVideo"
                  v-model="translateForm.video"
                  type="text"
                  placeholder="视频 URL 会自动复用下载到的视频；本地字幕可手动选择"
                  :disabled="isBusy || !ffmpegAvailable"
                >
                <button
                  id="chooseTranslateVideo"
                  class="secondary-button"
                  type="button"
                  :disabled="isBusy || !ffmpegAvailable"
                  @click="chooseTranslateVideo"
                >
                  选择
                </button>
              </div>
            </label>
          </section>
          <details class="advanced-panel span-2">
            <summary>高级配置</summary>
            <label class="check-row">
              <input
                id="useYamlConfig"
                v-model="translateForm.useYamlConfig"
                type="checkbox"
                :disabled="isBusy"
              >
              <span>使用 YAML 配置文件覆盖服务商和模型选择</span>
            </label>
            <label>
              <span>配置文件</span>
              <div class="inline-control">
                <input
                  id="translateConfig"
                  v-model="translateForm.config"
                  name="config"
                  type="text"
                  placeholder="默认配置"
                  :disabled="!translateForm.useYamlConfig || isBusy"
                >
                <button
                  id="chooseTranslateConfig"
                  class="secondary-button"
                  type="button"
                  :disabled="!translateForm.useYamlConfig || isBusy"
                  @click="chooseTranslateConfig"
                >
                  选择
                </button>
              </div>
            </label>
          </details>
          <label class="check-row">
            <input
              id="resumeTranslate"
              v-model="translateForm.resume"
              name="resume"
              type="checkbox"
              :disabled="isBusy"
            >
            <span>从断点继续</span>
          </label>
          <div class="actions span-2">
            <button
              id="startTranslate"
              class="primary-button"
              type="submit"
              :disabled="!canStartTranslate"
            >
              开始翻译
            </button>
          </div>
        </form>
        <form
          id="muxForm"
          class="form-grid mux-form"
          @submit.prevent="submitMux"
        >
          <div class="span-2 form-section-heading">
            <h3>已有字幕生成 MKV</h3>
            <p>翻译后想补生成视频时，在这里选择字幕和视频即可。</p>
          </div>
          <label>
            <span>已翻译字幕</span>
            <div class="inline-control">
              <input
                id="muxSubtitle"
                v-model="muxForm.subtitle"
                type="text"
                placeholder="output.zh.srt"
                :disabled="isBusy || !ffmpegAvailable"
              >
              <button
                id="chooseMuxSubtitle"
                class="secondary-button"
                type="button"
                :disabled="isBusy || !ffmpegAvailable"
                @click="chooseMuxSubtitle"
              >
                选择
              </button>
            </div>
          </label>
          <label>
            <span>视频文件</span>
            <div class="inline-control">
              <input
                id="muxVideo"
                v-model="muxForm.video"
                type="text"
                placeholder="video.mp4 / video.mkv"
                :disabled="isBusy || !ffmpegAvailable"
              >
              <button
                id="chooseMuxVideo"
                class="secondary-button"
                type="button"
                :disabled="isBusy || !ffmpegAvailable"
                @click="chooseMuxVideo"
              >
                选择
              </button>
            </div>
          </label>
          <label>
            <span>字幕语言</span>
            <input
              id="muxTargetLanguage"
              v-model="muxForm.targetLanguage"
              type="text"
              :disabled="isBusy || !ffmpegAvailable"
            >
          </label>
          <div class="actions">
            <button
              id="startMux"
              class="primary-button"
              type="submit"
              :disabled="!canStartMux"
            >
              生成 MKV
            </button>
          </div>
        </form>
      </section>

      <section
        :class="['task-panel', { 'is-active': activeTab === 'download' }]"
        data-panel="download"
        aria-labelledby="downloadTitle"
      >
        <div class="panel-heading">
          <div>
            <h2 id="downloadTitle">
              下载字幕
            </h2>
            <p>视频地址与字幕语言</p>
          </div>
        </div>
        <form
          id="downloadForm"
          class="form-grid"
          @submit.prevent="submitDownload"
        >
          <label class="span-2">
            <span>视频 URL</span>
            <input
              id="downloadUrl"
              v-model="downloadForm.url"
              name="url"
              type="url"
              placeholder="https://..."
              required
              :disabled="isBusy"
            >
          </label>
          <label>
            <span>源语言</span>
            <input
              id="downloadSourceLanguage"
              v-model="downloadForm.sourceLanguage"
              name="sourceLanguage"
              type="text"
              :disabled="isBusy"
            >
          </label>
          <label>
            <span>输出文件夹</span>
            <div class="inline-control">
              <input
                id="downloadOutputDir"
                v-model="downloadForm.outputDir"
                name="outputDir"
                type="text"
                :disabled="isBusy"
              >
              <button
                id="chooseDownloadDir"
                class="secondary-button"
                type="button"
                :disabled="isBusy"
                @click="chooseDownloadDir"
              >选择</button>
            </div>
          </label>
          <div class="actions span-2">
            <button
              class="primary-button"
              type="submit"
              :disabled="isBusy"
            >
              开始下载
            </button>
          </div>
        </form>
      </section>

      <section
        :class="['task-panel', { 'is-active': activeTab === 'transcribe' }]"
        data-panel="transcribe"
        aria-labelledby="transcribeTitle"
      >
        <div class="panel-heading">
          <div>
            <h2 id="transcribeTitle">
              音频转写
            </h2>
            <p>生成 SRT 后可继续翻译</p>
          </div>
          <button
            id="chooseAudio"
            class="secondary-button"
            type="button"
            :disabled="isBusy"
            @click="chooseAudio"
          >
            选择音频
          </button>
        </div>
        <form
          id="transcribeForm"
          class="form-grid"
          @submit.prevent="submitTranscribe"
        >
          <label class="span-2">
            <span>音频文件</span>
            <input
              id="audioInput"
              v-model="transcribeForm.audio"
              name="audio"
              type="text"
              required
              :disabled="isBusy"
            >
          </label>
          <label>
            <span>语言</span>
            <input
              id="transcribeLanguage"
              v-model="transcribeForm.language"
              name="language"
              type="text"
              :disabled="isBusy"
            >
          </label>
          <label>
            <span>输出 SRT</span>
            <div class="inline-control">
              <input
                id="transcribeOutput"
                v-model="transcribeForm.output"
                name="output"
                type="text"
                required
                :disabled="isBusy"
              >
              <button
                id="chooseTranscribeOutput"
                class="secondary-button"
                type="button"
                :disabled="isBusy"
                @click="chooseTranscribeOutput"
              >
                选择
              </button>
            </div>
          </label>
          <label class="span-2">
            <span>配置文件</span>
            <div class="inline-control">
              <input
                id="transcribeConfig"
                v-model="transcribeForm.config"
                name="config"
                type="text"
                placeholder="默认配置"
                :disabled="isBusy"
              >
              <button
                id="chooseTranscribeConfig"
                class="secondary-button"
                type="button"
                :disabled="isBusy"
                @click="chooseTranscribeConfig"
              >
                选择
              </button>
            </div>
          </label>
          <div class="actions span-2">
            <button
              class="primary-button"
              type="submit"
              :disabled="isBusy"
            >
              开始转写
            </button>
          </div>
        </form>
      </section>

      <section
        :class="['task-panel', { 'is-active': activeTab === 'settings' }]"
        data-panel="settings"
        aria-labelledby="settingsTitle"
      >
        <div class="panel-heading">
          <div>
            <h2 id="settingsTitle">
              设置
            </h2>
            <p>管理翻译服务 API Key</p>
          </div>
        </div>
        <div
          id="settingsCards"
          class="settings-grid"
        >
          <article
            v-for="provider in providers"
            :key="provider.id"
            class="settings-card"
            :data-provider-card="provider.id"
          >
            <div class="settings-card-heading">
              <div>
                <h3>{{ provider.name }}</h3>
                <p>{{ provider.credential.envKey }}</p>
              </div>
              <span :class="statusPillClass(provider.credential)">{{ provider.credential.label }}</span>
            </div>
            <label>
              <span>{{ provider.name }} API Key</span>
              <input
                v-model="apiKeyDrafts[provider.id]"
                :data-api-key-input="provider.id"
                type="password"
                autocomplete="off"
                placeholder="粘贴新的 API Key"
                :disabled="isBusy"
              >
            </label>
            <div class="card-actions">
              <button
                class="primary-button"
                type="button"
                :data-save-key="provider.id"
                :disabled="isBusy"
                @click="saveSettingsKey(provider.id)"
              >
                保存
              </button>
              <button
                class="secondary-button"
                type="button"
                :data-clear-key="provider.id"
                :disabled="isBusy"
                @click="clearSettingsKey(provider.id)"
              >
                清除
              </button>
            </div>
          </article>
        </div>
      </section>

      <section
        class="run-panel"
        aria-labelledby="runTitle"
      >
        <div class="run-heading">
          <div>
            <h2 id="runTitle">
              任务日志
            </h2>
            <p id="runStatus">
              {{ runStatus }}
            </p>
          </div>
          <div class="run-actions">
            <button
              id="openOutput"
              class="secondary-button"
              type="button"
              :disabled="!lastOutputPath"
              @click="openOutput"
            >
              打开输出
            </button>
            <button
              id="showOutput"
              class="secondary-button"
              type="button"
              :disabled="!lastOutputPath"
              @click="showOutput"
            >
              定位文件
            </button>
            <button
              id="cancelJob"
              class="danger-button"
              type="button"
              :disabled="!isBusy"
              @click="cancelJob"
            >
              取消
            </button>
            <button
              id="clearLog"
              class="secondary-button"
              type="button"
              @click="clearLog"
            >
              清空
            </button>
          </div>
        </div>
        <div
          id="resultFiles"
          :class="['result-files', { 'is-hidden': !hasAnyResult }]"
        >
          <div
            id="subtitleResultRow"
            :class="['result-file-row', { 'is-hidden': !hasSubtitleResult }]"
          >
            <div>
              <strong>字幕文件</strong>
              <span id="subtitleResultPath">{{ lastSubtitlePath }}</span>
            </div>
            <div class="result-actions">
              <button
                class="secondary-button"
                type="button"
                data-open-result="subtitle"
                @click="openResult('subtitle')"
              >
                打开
              </button>
              <button
                class="secondary-button"
                type="button"
                data-show-result="subtitle"
                @click="showResult('subtitle')"
              >
                定位
              </button>
            </div>
          </div>
          <div
            id="videoResultRow"
            :class="['result-file-row', { 'is-hidden': !hasVideoResult }]"
          >
            <div>
              <strong>带字幕 MKV</strong>
              <span id="videoResultPath">{{ lastEmbeddedVideoPath }}</span>
            </div>
            <div class="result-actions">
              <button
                class="secondary-button"
                type="button"
                data-open-result="video"
                @click="openResult('video')"
              >
                打开
              </button>
              <button
                class="secondary-button"
                type="button"
                data-show-result="video"
                @click="showResult('video')"
              >
                定位
              </button>
            </div>
          </div>
        </div>
        <pre
          id="logBody"
          ref="logBody"
          aria-live="polite"
        >{{ logText }}</pre>
      </section>
    </main>
  </div>
</template>
