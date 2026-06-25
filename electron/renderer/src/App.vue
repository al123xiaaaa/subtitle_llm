<script setup lang="ts">
import { useAppController } from "./composables/useAppController";

const {
  activeTab,
  apiKeyDrafts,
  canStartMux,
  canStartTranslate,
  cancelJob,
  chunkStatusLabel,
  chunkTooltip,
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
  configDrawerOpen,
  configureProvider,
  configureProviderText,
  customModelInput,
  downloadForm,
  drawerCloseIcon,
  drawerSubtitle,
  ffmpegAvailable,
  hasAnyResult,
  hasSubtitleResult,
  hasTraceResult,
  hasVideoResult,
  isBusy,
  lastEmbeddedVideoPath,
  lastLlmTraceDir,
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
  progressChunkSummary,
  progressChunks,
  progressLongWaitHint,
  progressStages,
  progressState,
  progressWaitText,
  runStatus,
  runtimeInfo,
  saveOnboardingKey,
  saveSettingsKey,
  selectChunk,
  selectedProgressChunk,
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
  toggleConfigDrawer,
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
          @click="setActiveTab(tab.id)"
        >
          <component :is="tab.icon" />
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

    <div class="workspace">
      <section
        class="run-panel"
        aria-labelledby="runTitle"
      >
        <div class="run-heading">
          <div>
            <h2 id="runTitle">
              工作进度
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
          id="progressDashboard"
          class="progress-dashboard"
        >
          <div class="current-progress">
            <div>
              <span class="progress-kicker">{{ progressState.currentLabel }}</span>
              <strong id="progressCurrentMessage">{{ progressState.currentMessage }}</strong>
            </div>
            <span
              id="progressWaitText"
              class="progress-wait"
            >{{ progressWaitText }}</span>
          </div>
          <p
            id="progressLongWaitHint"
            :class="['progress-hint', { 'is-hidden': !progressLongWaitHint }]"
          >
            {{ progressLongWaitHint }}
          </p>
          <div
            id="progressTimeline"
            class="progress-timeline"
            aria-label="任务阶段"
          >
            <div
              v-for="stage in progressStages"
              :key="stage.id"
              :class="['progress-stage', `is-${stage.status}`]"
            >
              <span class="stage-dot" />
              <span>{{ stage.label }}</span>
            </div>
          </div>
          <section
            id="chunkActivity"
            :class="['chunk-activity', { 'is-hidden': progressChunks.length === 0 }]"
            aria-labelledby="chunkActivityTitle"
          >
            <div class="chunk-activity-heading">
              <div>
                <h3 id="chunkActivityTitle">
                  片段活动
                </h3>
                <p id="chunkActivitySummary">
                  {{ progressChunkSummary }}
                </p>
              </div>
              <span class="status-pill is-env">并发</span>
            </div>
            <div
              class="chunk-grid"
              role="list"
              aria-label="Chunk activity"
            >
              <button
                v-for="chunk in progressChunks"
                :key="chunk.index"
                type="button"
                :class="['chunk-cell', `is-${chunk.status}`, { 'is-selected': selectedProgressChunk?.index === chunk.index }]"
                :title="chunkTooltip(chunk)"
                :aria-label="chunkTooltip(chunk)"
                @click="selectChunk(chunk.index)"
              />
            </div>
            <div
              v-if="selectedProgressChunk"
              id="chunkActivityDetail"
              class="chunk-detail"
            >
              <div>
                <strong>Chunk {{ selectedProgressChunk.index }} / {{ selectedProgressChunk.total }}</strong>
                <span>{{ chunkStatusLabel(selectedProgressChunk.status) }}</span>
              </div>
              <p>{{ selectedProgressChunk.message }}</p>
              <p>
                字幕
                {{ selectedProgressChunk.entryStart || "?" }}
                -
                {{ selectedProgressChunk.entryEnd || "?" }}
                <span v-if="selectedProgressChunk.model"> · {{ selectedProgressChunk.model }}</span>
                <span v-if="selectedProgressChunk.durationMs"> · {{ (selectedProgressChunk.durationMs / 1000).toFixed(1) }}s</span>
                <span v-if="selectedProgressChunk.traceId"> · trace {{ selectedProgressChunk.traceId }}</span>
              </p>
              <p v-if="selectedProgressChunk.issueSummary">
                {{ selectedProgressChunk.issueSummary }}
              </p>
            </div>
          </section>
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
          <div
            id="traceResultRow"
            :class="['result-file-row', { 'is-hidden': !hasTraceResult }]"
          >
            <div>
              <strong>LLM 诊断</strong>
              <span id="traceResultPath">{{ lastLlmTraceDir }}</span>
            </div>
            <div class="result-actions">
              <button
                class="secondary-button"
                type="button"
                data-open-result="trace"
                @click="openResult('trace')"
              >
                打开
              </button>
              <button
                class="secondary-button"
                type="button"
                data-show-result="trace"
                @click="showResult('trace')"
              >
                定位
              </button>
            </div>
          </div>
        </div>
        <details
          id="logDetails"
          class="log-details"
        >
          <summary>详细日志</summary>
          <pre
            id="logBody"
            ref="logBody"
            aria-live="polite"
          >{{ logText }}</pre>
        </details>
      </section>

      <aside
        id="configDrawer"
        :class="['config-drawer', { 'is-collapsed': !configDrawerOpen }]"
        aria-label="任务配置"
      >
        <div class="drawer-rail">
          <button
            v-for="tab in taskTabs"
            :key="tab.id"
            type="button"
            :class="['rail-tab', { 'is-active': activeTab === tab.id }]"
            :title="tab.label"
            :data-rail-tab="tab.id"
            @click="setActiveTab(tab.id)"
          >
            <component :is="tab.icon" />
          </button>
        </div>
        <div class="drawer-content">
          <div class="drawer-heading">
            <div>
              <h2>{{ taskTabs.find((t) => t.id === activeTab)?.label }}</h2>
              <p>{{ drawerSubtitle }}</p>
            </div>
            <button
              id="collapseDrawer"
              class="icon-button"
              type="button"
              title="收起配置"
              @click="toggleConfigDrawer"
            >
              <component :is="drawerCloseIcon" />
            </button>
          </div>

          <section
            :class="['task-panel', { 'is-active': activeTab === 'translate' }]"
            data-panel="translate"
            aria-labelledby="translateTitle"
          >
            <form
              id="translateForm"
              class="form-grid"
              @submit.prevent="submitTranslate"
            >
              <div class="form-group">
                <h3 class="form-group-title">
                  基础
                </h3>
                <div class="form-fields">
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
                </div>
              </div>

              <div class="form-group">
                <h3 class="form-group-title">
                  模型
                </h3>
                <div class="form-fields">
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
                </div>
              </div>

              <div class="form-group">
                <h3 class="form-group-title">
                  输出
                </h3>
                <div class="form-fields">
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
                    <span>源语言</span>
                    <input
                      id="sourceLanguage"
                      v-model="translateForm.sourceLanguage"
                      name="sourceLanguage"
                      type="text"
                      :disabled="isBusy"
                    >
                  </label>
                </div>
              </div>

              <div class="form-group">
                <h3 class="form-group-title">
                  复核与高级
                </h3>
                <div class="form-fields">
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
                  <label class="check-row">
                    <input
                      id="refineTranslation"
                      v-model="translateForm.refineTranslation"
                      type="checkbox"
                      :disabled="isBusy"
                    >
                    <span>启用二次润色</span>
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
                </div>
              </div>

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
              <div class="form-section-heading">
                <h3>已有字幕生成 MKV</h3>
                <p>翻译后想补生成视频时，在这里选择字幕和视频即可。</p>
              </div>
              <div class="form-group">
                <div class="form-fields">
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
                </div>
              </div>
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
            <h2
              id="downloadTitle"
              class="is-hidden"
            >
              下载字幕
            </h2>
            <form
              id="downloadForm"
              class="form-grid"
              @submit.prevent="submitDownload"
            >
              <div class="form-group">
                <div class="form-fields">
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
                </div>
              </div>
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
            <h2
              id="transcribeTitle"
              class="is-hidden"
            >
              音频转写
            </h2>
            <form
              id="transcribeForm"
              class="form-grid"
              @submit.prevent="submitTranscribe"
            >
              <div class="form-group">
                <div class="form-fields">
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
                </div>
              </div>
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
            <h2
              id="settingsTitle"
              class="is-hidden"
            >
              设置
            </h2>
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
        </div>
      </aside>
    </div>
  </div>
</template>
