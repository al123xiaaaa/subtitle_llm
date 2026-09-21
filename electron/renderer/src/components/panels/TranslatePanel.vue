<script setup lang="ts">
import { toRefs } from "vue";
import { ChevronDown, Save, ShieldCheck, SlidersHorizontal } from "@lucide/vue";
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
  isAsrDeviceAuto,
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
const {
  canStartMux,
  canStartTranslate,
  inspectReusableResult,
  reusableSubtitle,
  submitMux,
  submitTranslate,
  reuseSourceSubtitle,
  existingTranslation,
  translationSubmitting,
} = props.formActions;

function formatReuseTime(createdAt: string): string {
  const time = new Date(createdAt);
  return Number.isNaN(time.getTime()) ? createdAt : time.toLocaleString();
}
const {
  summaryProviderId,
  summaryModelId,
  configureProvider,
  configureProviderText,
  customModelInput,
  translationMaxTokensInput,
  outputBudgetError,
  onOutputBudgetInput,
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
    :class="['task-panel', 'translate-panel', { 'is-active': activeTab === 'translate' }]"
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
          翻译内容
        </h3>
        <div class="form-fields">
          <label class="span-2">
            <span>字幕文件或视频链接</span>
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
          <div
            v-if="reusableSubtitle"
            id="reuseSubtitleBanner"
            class="reuse-banner span-2"
          >
            <div class="reuse-banner-text">
              <strong>{{ existingTranslation ? "这个视频已有译文" : "发现历史字幕" }}</strong>
              <span>《{{ reusableSubtitle.title }}》生成于 {{ formatReuseTime(reusableSubtitle.createdAt) }}{{ existingTranslation ? "，可直接查看已有结果" : "，选择源字幕处理方式后点开始翻译" }}</span>
            </div>
            <div class="reuse-banner-actions">
              <button
                v-if="existingTranslation"
                id="reuseSubtitleView"
                class="primary-button"
                type="button"
                :disabled="isBusy"
                @click="inspectReusableResult"
              >
                查看结果
              </button>
              <fieldset
                v-if="reusableSubtitle.subtitlePath"
                class="reuse-options"
              >
                <legend>重新翻译时使用</legend>
                <label class="check-row">
                  <input
                    id="reuseSubtitleUse"
                    v-model="reuseSourceSubtitle"
                    type="radio"
                    name="subtitleSource"
                    :value="true"
                    :disabled="isBusy || translationSubmitting"
                  >
                  <span>复用源字幕（跳过下载与 ASR）</span>
                </label>
                <label class="check-row">
                  <input
                    id="reuseSubtitleRegenerate"
                    v-model="reuseSourceSubtitle"
                    type="radio"
                    name="subtitleSource"
                    :value="false"
                    :disabled="isBusy || translationSubmitting"
                  >
                  <span>重新获取源字幕</span>
                </label>
              </fieldset>
              <span v-else>源字幕已不存在，重新翻译时将重新获取。</span>
            </div>
          </div>
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

      <details class="span-2 translate-options">
        <summary>
          <span class="translation-options-title"><SlidersHorizontal :size="15" />本次设置<ChevronDown
            :size="14"
            class="translation-options-chevron"
          /></span>
          <span class="translation-model-summary">{{ translateForm.useYamlConfig ? '按 YAML 配置' : (showCustomModelInput ? customModelInput : selectedModelId) || '选择翻译模型' }}</span>
          <span class="translation-option-tags"><span>{{ translateForm.semanticCheck ? 'Jev 检查' : '稍后补查' }}</span><span>{{ translateForm.embedMkv ? '双语字幕 + 视频' : '字幕文件' }}</span></span>
        </summary>
        <div class="form-group">
          <h3 class="form-group-title">
            模型
          </h3>
          <div class="form-fields">
            <label class="span-2">
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
            <label class="span-2">
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
              >
            </label>
            <label class="span-2">
              <span>翻译输出上限（tokens，可选）</span>
              <input
                id="translationMaxTokens"
                v-model="translationMaxTokensInput"
                type="number"
                min="1"
                step="1"
                :max="Number.MAX_SAFE_INTEGER"
                placeholder="使用服务商默认值"
                :disabled="isBusy || translateForm.useYamlConfig"
                :aria-invalid="Boolean(outputBudgetError)"
                aria-describedby="outputBudgetHelp"
                @input="onOutputBudgetInput"
              >
              <small id="outputBudgetHelp">
                {{ translateForm.useYamlConfig ? "使用 YAML 中的输出上限，不覆盖配置文件。" : outputBudgetError || "留空使用服务商默认值；仅限制翻译输出，摘要使用服务商默认值。" }}
              </small>
            </label>
            <label class="span-2"><span>摘要模型服务（可独立选择）</span><select
              v-model="summaryProviderId"
              :disabled="isBusy"
            ><option value="">沿用本次翻译服务</option><option
              v-for="provider in providers"
              :key="provider.id"
              :value="provider.id"
            >{{ provider.name }}</option></select></label>
            <label class="span-2"><span>摘要模型 ID（留空沿用翻译模型）</span><input
              v-model="summaryModelId"
              :disabled="isBusy"
              placeholder="只负责摘要和术语上下文"
            ></label>
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
                :disabled="isBusy || Boolean(reusableSubtitle?.subtitlePath && reuseSourceSubtitle)"
              >
              <span>{{ reusableSubtitle?.subtitlePath && reuseSourceSubtitle ? "复用源字幕时不执行 ASR" : "不下载原字幕，改用 ASR" }}</span>
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
                :disabled="isBusy || isAsrDeviceAuto(translateForm.asrModel)"
              >
                <option value="">
                  {{ isAsrDeviceAuto(translateForm.asrModel) ? "自动（Metal GPU）" : "默认（CPU）" }}
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
      </details>
      <section
        class="translation-quality"
        aria-labelledby="translationQualityTitle"
      >
        <h3 id="translationQualityTitle">
          <ShieldCheck :size="16" />质量检查
        </h3>
        <label class="check-row semantic-check-row">
          <input
            v-model="translateForm.semanticCheck"
            type="checkbox"
            :disabled="isBusy || translateForm.useYamlConfig"
          >
          <span>使用 Jev 语义检查<small>不可用时仍保留译文，标记为待补查。</small></span>
        </label>
        <div
          v-if="providerState.appState.value?.gatewayCredentialState === 'missing' && translateForm.semanticCheck && !translateForm.useYamlConfig"
          class="translation-alert"
        >
          <p>Jev 尚未配置凭据，请在本地配置 AI_GATEWAY_API_KEY。</p>
          <button
            type="button"
            class="secondary-button"
            @click="translateForm.semanticCheck = false"
          >
            仅翻译，稍后补查
          </button>
        </div>
        <p
          v-else-if="providerState.appState.value?.gatewayCredentialState === 'local-file' && translateForm.semanticCheck"
          class="translation-help"
        >
          将从本地配置加载 Jev 凭据；检测到配置文件不代表服务已验证可用。
        </p>
        <details class="translation-method">
          <summary>模型分工与额外额度</summary>
          <p>摘要用于理解内容；翻译模型负责译文和修复；Jev 只做判断。自动额外处理默认预留首轮 token 的 30%，费用未知。</p>
        </details>
      </section>
      <div class="translation-defaults">
        <button
          type="button"
          class="secondary-button"
          aria-label="将模型选择明确保存为默认"
          :disabled="isBusy"
          @click="persistProviderPreference"
        >
          <Save :size="14" />保存为默认设置
        </button>
        <p>保存翻译、摘要、转写与语义检查设置。本次临时选择不会自动保存。</p>
      </div>
      <div
        v-if="translateForm.embedMkv && (!ffmpegAvailable || (reusableSubtitle && reuseSourceSubtitle && !reusableSubtitle.videoPath && !translateForm.video))"
        class="translation-alert span-2"
      >
        <p>当前缺少生成视频的条件；可以补充视频 / FFmpeg，也可明确选择先完成字幕。</p>
        <button
          type="button"
          class="secondary-button"
          @click="translateForm.embedMkv = false"
        >
          本次仅翻译字幕
        </button>
      </div>
      <p
        v-if="translateForm.embedMkv"
        class="span-2 help-text"
      >
        生成视频会下载或使用原视频；字幕先独立保存，视频失败可稍后单独重试。
      </p>
      <div class="actions span-2 translation-submit">
        <button
          id="startTranslate"
          class="primary-button"
          type="submit"
          :disabled="!canStartTranslate || translationSubmitting"
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

<style src="../../styles/translation-form.css"></style>
