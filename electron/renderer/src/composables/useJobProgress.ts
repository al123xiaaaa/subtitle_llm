import { computed, onMounted, onUnmounted, ref } from "vue";
import type { CliProgressEvent, CommandName } from "../../../types";
import {
  applyProgressEvent,
  chunkLegendItems,
  chunkSummary,
  chunkTokenRateText,
  chunkTooltip,
  createInitialJobProgressState,
  finishProgress,
  selectProgressChunk,
  selectedChunk,
  startProgress,
  statusLabel,
  tokenThroughputText,
} from "../../../lib/progressModel";

export function useJobProgress() {
  const progressState = ref(createInitialJobProgressState());
  const now = ref(Date.now());
  let timer: ReturnType<typeof setInterval> | null = null;

  const progressStages = computed(() => progressState.value.stages);
  const progressChunks = computed(() => progressState.value.chunks);
  const progressChunkLegendItems = computed(() => chunkLegendItems(progressState.value.chunks));
  const selectedProgressChunk = computed(() => selectedChunk(progressState.value));
  const progressChunkSummary = computed(() => chunkSummary(progressState.value.chunks));
  // 进度主区状态视觉：空闲/运行/完成/失败/带风险，驱动 current-progress 卡片配色。
  const progressStatusClass = computed(() => `is-${progressState.value.currentStatus}`);
  const progressIsEmpty = computed(
    () => progressState.value.currentStatus === "waiting" && progressChunks.value.length === 0,
  );
  const progressWaitSeconds = computed(() => {
    if (!progressState.value.lastActivityAt || progressState.value.currentStatus !== "running") {
      return 0;
    }
    return Math.max(0, Math.floor((now.value - progressState.value.lastActivityAt) / 1000));
  });
  const progressWaitText = computed(() => {
    if (!progressState.value.lastActivityAt) {
      return "";
    }
    if (progressWaitSeconds.value <= 0) {
      return "刚刚更新";
    }
    return `已等待 ${formatSeconds(progressWaitSeconds.value)}`;
  });
  const progressLongWaitHint = computed(() =>
    progressWaitSeconds.value >= 90 ? "仍在等待响应，任务没有被标记为失败。" : "",
  );
  const progressTokenText = computed(() => tokenThroughputText(progressState.value.runUsage));

  function resetProgress(command: CommandName): void {
    progressState.value = startProgress(command);
  }

  function clearProgress(): void {
    progressState.value = createInitialJobProgressState();
  }

  function recordProgress(event: CliProgressEvent): void {
    progressState.value = applyProgressEvent(progressState.value, event);
  }

  function finishJobProgress(ok: boolean): void {
    progressState.value = finishProgress(progressState.value, ok);
  }

  function selectChunk(index: number): void {
    progressState.value = selectProgressChunk(progressState.value, index);
  }

  onMounted(() => {
    timer = setInterval(() => {
      now.value = Date.now();
    }, 1000);
  });

  onUnmounted(() => {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  });

  return {
    chunkStatusLabel: statusLabel,
    chunkTokenRateText,
    chunkTooltip,
    clearProgress,
    finishJobProgress,
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
    recordProgress,
    resetProgress,
    selectChunk,
    selectedProgressChunk,
  };
}

function formatSeconds(value: number): string {
  const minutes = Math.floor(value / 60);
  const seconds = value % 60;
  if (minutes <= 0) {
    return `${seconds}s`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
