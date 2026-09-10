<script setup lang="ts">
import { toRefs } from "vue";
import { AudioLines } from "@lucide/vue";
import type { useAppController } from "../../composables/useAppController";
import type { TaskTab } from "../../composables/controllerTypes";

type Controller = ReturnType<typeof useAppController>;

const props = defineProps<{
  activeTab: TaskTab;
  formActions: Controller["formActions"];
  forms: Controller["forms"];
  isBusy: boolean;
}>();

const { activeTab, isBusy } = toRefs(props);
const { asrModels, chooseTranscribeConfig, chooseTranscribeOutput, defaultAsrModel, isAsrDeviceAuto, transcribeForm } = props.forms;
const { submitTranscribe } = props.formActions;
</script>

<template>
  <section
    :class="['task-panel', { 'is-active': activeTab === 'transcribe' }]"
    data-panel="transcribe"
    aria-label="音频转写"
  >
    <form
      id="transcribeForm"
      class="form-grid"
      @submit.prevent="submitTranscribe"
    >
      <div class="panel-intro">
        <AudioLines />
        <p>把音频或视频文件转成带时间轴的字幕（SRT），转写完可以直接去翻译。</p>
      </div>
      <div class="form-group">
        <div class="form-group-title">
          基础
        </div>
        <div class="form-fields">
          <label class="span-2">
            <span>音频或视频文件</span>
            <input
              id="audioInput"
              v-model="transcribeForm.audio"
              name="audio"
              type="text"
              placeholder="选择 .wav / .mp3 / .mp4 等文件"
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
            <span>输出字幕</span>
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
        </div>
      </div>
      <div class="form-group">
        <div class="form-group-title">
          识别引擎
        </div>
        <div class="form-fields">
          <label>
            <span>ASR 模型</span>
            <select
              id="transcribeAsrModel"
              v-model="transcribeForm.asrModel"
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
          <label>
            <span>运行设备</span>
            <select
              id="transcribeAsrDevice"
              v-model="transcribeForm.asrDevice"
              :disabled="isBusy || isAsrDeviceAuto(transcribeForm.asrModel)"
            >
              <option value="">
                {{ isAsrDeviceAuto(transcribeForm.asrModel) ? "自动（Metal GPU）" : "默认（CPU）" }}
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
          <label class="span-2">
            <span>自定义配置</span>
            <div class="inline-control">
              <input
                id="transcribeConfig"
                v-model="transcribeForm.config"
                name="config"
                type="text"
                placeholder="留空使用默认配置"
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
</template>
