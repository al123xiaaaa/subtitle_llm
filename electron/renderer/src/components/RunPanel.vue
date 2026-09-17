<script setup lang="ts">
import { computed, ref } from "vue";
import { Languages } from "@lucide/vue";
import SubtitlePreview from "./SubtitlePreview.vue";
import type { useAppController } from "../composables/useAppController";

type Controller = ReturnType<typeof useAppController>;

const props = defineProps<{
  job: Controller["job"];
}>();

const {
  activeCommand,
  subtitlePreview,
  activeJobId,
  cancelJob,
  chunkStatusLabel,
  chunkTokenRateText,
  chunkTooltip,
  clearLog,
  copyLog,
  elapsedText,
  hasAnyResult,
  hasSourceVideoResult,
  hasSubtitleResult,
  hasTraceResult,
  hasVideoResult,
  lastEmbeddedVideoPath,
  lastLlmTraceDir,
  lastSourceVideoPath,
  lastSubtitlePath,
  latestLogLine,
  logAutoScroll,
  logBody,
  logLines,
  openResult,
  progressChunkLegendItems,
  progressChunkSummary,
  progressChunks,
  progressIsEmpty,
  progressLongWaitHint,
  progressStages,
  progressState,
  progressStatusClass,
  progressTokenText,
  progressWaitText,
  runStatus,
  selectChunk,
  selectedProgressChunk,
  showResult,
} = props.job;

// 任务结束后片段网格收成一行摘要（进度区仍是主角，但完成态不再被
// 满屏绿格子稀释）；用户可展开回看。运行中永远展开。
const chunkExpanded = ref(false);
const isDone = computed(() => progressChunks.value.length > 0 && !activeJobId.value);
const chunksCollapsed = computed(() => isDone.value && !chunkExpanded.value);

// 失败时日志自动展开——错误永远不该藏在折叠区里
const logAutoOpen = computed(
  () => runStatus.value.startsWith("失败") || runStatus.value.startsWith("退出码"),
);

const resultFiles = computed(() => [
  { kind: "subtitle", id: "subtitle", label: "双语字幕", path: lastSubtitlePath.value, visible: hasSubtitleResult.value },
  { kind: "video", id: "video", label: "带字幕 MKV", path: lastEmbeddedVideoPath.value, visible: hasVideoResult.value },
  { kind: "source", id: "sourceVideo", label: "下载的视频", path: lastSourceVideoPath.value, visible: hasSourceVideoResult.value },
  { kind: "trace", id: "trace", label: "LLM 诊断", path: lastLlmTraceDir.value, visible: hasTraceResult.value },
] as const);

function fileName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) || path;
}
</script>

<template>
  <section
    :class="['run-panel', { 'is-idle': progressIsEmpty }]"
    aria-labelledby="runTitle"
  >
    <div class="run-heading">
      <div>
        <h2 id="runTitle">
          当前任务
        </h2>
        <p id="runStatus">
          {{ runStatus }}
        </p>
      </div>
      <div
        v-if="activeJobId"
        class="run-actions"
      >
        <button
          id="cancelJob"
          class="danger-button"
          type="button"
          @click="cancelJob"
        >
          取消
        </button>
      </div>
    </div>
    <div
      id="progressDashboard"
      class="progress-dashboard"
    >
      <div
        v-if="!progressIsEmpty"
        :class="['current-progress', progressStatusClass]"
      >
        <div>
          <span class="progress-kicker">{{ progressState.currentLabel }}</span>
          <strong id="progressCurrentMessage">{{ progressState.currentMessage }}</strong>
        </div>
        <span
          id="progressWaitText"
          class="progress-wait"
        >{{ progressWaitText }}</span>
      </div>
      <div
        v-if="progressIsEmpty"
        class="empty-hero"
      >
        <div class="empty-hero-mark">
          <Languages />
        </div>
        <h2>把字幕翻译成任何语言</h2>
        <p class="progress-empty-hint">
          粘贴视频链接或选择本地字幕文件，下载、转写、翻译、封装一次完成。
        </p>
        <div class="empty-hero-steps">
          <span>粘贴链接</span>
          <span>选择模型</span>
          <span>开始翻译</span>
        </div>
      </div>
      <p
        id="progressLongWaitHint"
        :class="['progress-hint', { 'is-hidden': !progressLongWaitHint }]"
      >
        {{ progressLongWaitHint }}
      </p>
      <div :class="['run-detail-grid', { 'has-results': hasAnyResult }]">
        <div class="run-process">
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
              <div class="chunk-activity-actions">
                <span class="status-pill is-env">并发</span>
                <button
                  v-if="isDone"
                  id="toggleChunkActivity"
                  class="ghost-button"
                  type="button"
                  :aria-expanded="!chunksCollapsed"
                  @click="chunkExpanded = !chunkExpanded"
                >
                  {{ chunksCollapsed ? "展开" : "收起" }}
                </button>
              </div>
            </div>
            <template v-if="!chunksCollapsed">
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
                class="chunk-legend"
                aria-hidden="true"
              >
                <span
                  v-for="legend in progressChunkLegendItems"
                  :key="legend.status"
                  class="chunk-legend-item"
                >
                  <i :class="['legend-swatch', `is-${legend.status}`]" />{{ legend.label }}
                </span>
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
                  <span v-if="chunkTokenRateText(selectedProgressChunk)"> · {{ chunkTokenRateText(selectedProgressChunk) }}</span>
                  <span v-if="selectedProgressChunk.traceId"> · trace {{ selectedProgressChunk.traceId }}</span>
                </p>
                <p v-if="selectedProgressChunk.issueSummary">
                  {{ selectedProgressChunk.issueSummary }}
                </p>
              </div>
            </template>
          </section>
          <SubtitlePreview
            v-if="activeCommand === 'translate'"
            :snapshot="subtitlePreview"
            :running="Boolean(activeJobId) || runStatus === '启动中' || runStatus === '运行中'"
          />
        </div>
        <section
          id="resultFiles"
          :class="['result-files', { 'is-hidden': !hasAnyResult }]"
          aria-labelledby="resultFilesTitle"
        >
          <div class="result-heading">
            <h3 id="resultFilesTitle">
              输出文件
            </h3>
            <p>打开结果，或在文件夹中查看</p>
          </div>
          <template
            v-for="file in resultFiles"
            :key="file.kind"
          >
            <article
              v-if="file.visible"
              :id="`${file.id}ResultRow`"
              class="result-file-row"
            >
              <div>
                <span class="result-file-label">{{ file.label }}</span>
                <strong :title="file.path">{{ fileName(file.path) }}</strong>
                <span
                  :id="`${file.id}ResultPath`"
                  :title="file.path"
                >{{ file.path }}</span>
              </div>
              <div class="result-actions">
                <button
                  :class="file.kind === 'subtitle' ? 'primary-button' : 'secondary-button'"
                  type="button"
                  :data-open-result="file.kind"
                  :aria-label="`打开${file.label}`"
                  @click="openResult(file.kind)"
                >
                  打开
                </button>
                <button
                  class="ghost-button"
                  type="button"
                  :data-show-result="file.kind"
                  :aria-label="`在文件夹中显示${file.label}`"
                  @click="showResult(file.kind)"
                >
                  定位
                </button>
              </div>
            </article>
          </template>
        </section>
      </div>
    </div>
    <details
      id="logDetails"
      class="log-details"
      :open="logAutoOpen"
    >
      <summary>
        <span class="log-summary-title">运行日志</span>
        <span
          v-if="elapsedText"
          class="log-elapsed"
        >已进行 {{ elapsedText }}</span>
        <span
          v-if="progressTokenText"
          id="tokenThroughput"
          class="log-tokens"
        >{{ progressTokenText }}</span>
        <span
          v-if="latestLogLine"
          class="log-latest"
        >{{ latestLogLine }}</span>
      </summary>
      <div class="log-card">
        <div class="log-toolbar">
          <button
            id="copyLog"
            class="ghost-button"
            type="button"
            @click="copyLog"
          >
            复制
          </button>
          <button
            id="clearLog"
            class="ghost-button"
            type="button"
            @click="clearLog"
          >
            清空
          </button>
          <label class="log-autoscroll">
            <input
              v-model="logAutoScroll"
              type="checkbox"
            >
            自动滚动
          </label>
        </div>
        <div
          id="logBody"
          ref="logBody"
          class="log-body"
          aria-live="polite"
        >
          <div
            v-for="line in logLines"
            :key="line.id"
            :class="['log-line', `is-${line.kind}`]"
          >
            <span class="log-time">{{ line.time }}</span>
            <span class="log-text">{{ line.text }}</span>
          </div>
        </div>
      </div>
    </details>
  </section>
</template>
