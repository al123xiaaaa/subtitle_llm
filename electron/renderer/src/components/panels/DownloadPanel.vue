<script setup lang="ts">
import { toRefs } from "vue";
import { Download } from "@lucide/vue";
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
const { chooseDownloadDir, downloadForm } = props.forms;
const { submitDownload } = props.formActions;
</script>

<template>
  <section
    :class="['task-panel', { 'is-active': activeTab === 'download' }]"
    data-panel="download"
    aria-label="下载字幕"
  >
    <form
      id="downloadForm"
      class="form-grid"
      @submit.prevent="submitDownload"
    >
      <div class="panel-intro">
        <Download />
        <p>粘贴视频链接，自动下载字幕；没有字幕时下载视频和音频，可直接接着转写。</p>
      </div>
      <div class="form-group">
        <div class="form-fields">
          <label class="span-2">
            <span>视频链接</span>
            <input
              id="downloadUrl"
              v-model="downloadForm.url"
              name="url"
              type="url"
              placeholder="https://www.youtube.com/watch?v=..."
              required
              :disabled="isBusy"
            >
          </label>
          <label>
            <span>字幕语言</span>
            <input
              id="downloadSourceLanguage"
              v-model="downloadForm.sourceLanguage"
              name="sourceLanguage"
              type="text"
              :disabled="isBusy"
            >
          </label>
          <label>
            <span>保存到</span>
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
</template>
