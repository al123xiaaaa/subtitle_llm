<script setup lang="ts">
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
  hasAnyResult,
  hasSubtitleResult,
  hasTraceResult,
  hasVideoResult,
  lastEmbeddedVideoPath,
  lastLlmTraceDir,
  lastOutputPath,
  lastSubtitlePath,
  logBody,
  logText,
  openOutput,
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
  showOutput,
  showResult,
} = props.job;
</script>

<template>
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
          :disabled="!activeJobId"
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
      <div :class="['current-progress', progressStatusClass]">
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
        v-if="progressIsEmpty"
        class="progress-empty-hint"
      >
        从右侧选择一个任务并配置，然后开始。运行时这里会显示程序当前在做什么。
      </p>
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
</template>
