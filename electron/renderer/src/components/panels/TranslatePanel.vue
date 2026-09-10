<script setup lang="ts">
import { toRefs } from "vue";
import type { useAppController } from "../../composables/useAppController";
import type { TaskTab } from "../../composables/controllerTypes";

type Controller = ReturnType<typeof useAppController>;

const props = defineProps<{
  activeTab: TaskTab;
  formActions: Controller["formActions"];
  forms: Controller["forms"];
  isBusy: boolean;
  providerState: Controller["providerState"];
}>();

const {
  asrModels,
  chooseMuxSubtitle,
  chooseMuxVideo,
  chooseOutput,
  chooseTranslateConfig,
  chooseTranslateVideo,
  defaultAsrModel,
  mkvCapabilityClass,
  mkvCapabilityText,
  muxForm,
  onEmbedMkvChanged,
  onTranslateInput,
  translateForm,
} = props.forms;
const { canStartMux, canStartTranslate, submitMux, submitTranslate } = props.formActions;
const {
  configureProvider,
  configureProviderText,
  customModelInput,
  onModelChanged,
  onProviderChanged,
  persistProviderPreference,
  providerCredentialStatus,
  providerModels,
  providers,
  selectedModelId,
  selectedProviderId,
  showConfigureProvider,
  showCustomModelInput,
} = props.providerState;
const { activeTab, isBusy } = toRefs(props);
const { ffmpegAvailable } = props.providerState;
</script>

<template>
  <section
    :class="['task-panel', { 'is-active': activeTab === 'translate' }]"
    data-panel="translate"
    aria-label="翻译字幕"
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
              :class="['ghost-button', 'credential-action', { 'is-hidden': !showConfigureProvider }]"
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
                placeholder="留空自动命名：data/output/<标题>.<语言>.srt"
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
              <option value="">默认（双语 · 原文在前）</option>
              <option value="source-first">双语 · 原文在前</option>
              <option value="target-first">双语 · 译文在前</option>
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
              list="sourceLanguageOptions"
              placeholder="默认 en"
              :disabled="isBusy"
            >
            <datalist id="sourceLanguageOptions">
              <option value="en">英语</option>
              <option value="zh">中文</option>
              <option value="ja">日语</option>
              <option value="ko">韩语</option>
              <option value="yue">粤语</option>
              <option value="fr">法语</option>
              <option value="de">德语</option>
              <option value="es">西班牙语</option>
              <option value="it">意大利语</option>
              <option value="pt">葡萄牙语</option>
              <option value="ru">俄语</option>
            </datalist>
          </label>
          <label class="check-row span-2">
            <input
              id="forceAsr"
              v-model="translateForm.forceAsr"
              type="checkbox"
              :disabled="isBusy"
            >
            <span>不下载原字幕，改用 ASR</span>
          </label>
          <label
            v-if="translateForm.forceAsr"
            class="span-2"
          >
            <span>ASR 模型</span>
            <select
              id="asrModel"
              v-model="translateForm.asrModel"
              :disabled="isBusy"
            >
              <option value="">
                默认（{{ asrModels.find((model) => model.id === defaultAsrModel)?.label || defaultAsrModel }}）
              </option>
              <option
                v-for="model in asrModels"
                :key="model.id"
                :value="model.id"
                :title="model.description"
              >
                {{ model.label }}
              </option>
            </select>
          </label>
          <label v-if="translateForm.forceAsr">
            <span>ASR 设备</span>
            <select
              id="asrDevice"
              v-model="translateForm.asrDevice"
              :disabled="isBusy"
            >
              <option value="">
                默认（CPU）
              </option>
              <option value="cpu">
                CPU
              </option>
              <option value="mps">
                GPU（Apple MPS）
              </option>
              <option value="cuda">
                GPU（CUDA）
              </option>
            </select>
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
</template>
