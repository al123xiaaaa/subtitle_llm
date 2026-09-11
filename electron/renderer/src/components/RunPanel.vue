<script setup lang="ts">
import { computed, ref } from "vue";
import { Languages } from "@lucide/vue";
import type { useAppController } from "../composables/useAppController";

type Controller = ReturnType<typeof useAppController>;

const props = defineProps<{
  job: Controller["job"];
}>();

const {
  activeJobId,
  cancelJob,
  chunkStatusLabel,
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
              <span v-if="selectedProgressChunk.traceId"> · trace {{ selectedProgressChunk.traceId }}</span>
            </p>
            <p v-if="selectedProgressChunk.issueSummary">
              {{ selectedProgressChunk.issueSummary }}
            </p>
          </div>
        </template>
      </section>
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
        id="sourceVideoResultRow"
        :class="['result-file-row', { 'is-hidden': !hasSourceVideoResult }]"
      >
        <div>
          <strong>下载的视频</strong>
          <span id="sourceVideoResultPath">{{ lastSourceVideoPath }}</span>
        </div>
        <div class="result-actions">
          <button
            class="secondary-button"
            type="button"
            data-open-result="source"
            @click="openResult('source')"
          >
            打开
          </button>
          <button
            class="secondary-button"
            type="button"
            data-show-result="source"
            @click="showResult('source')"
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
