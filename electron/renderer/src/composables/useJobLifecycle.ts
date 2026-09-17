import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import type { Ref } from "vue";
import { Effect, Fiber, Stream } from "effect";
import type { CommandName, DesktopJobRequest, SubtitlePreviewSnapshot } from "../../../types";
import { applySubtitlePreview } from "../../../lib/subtitlePreview";
import { useJobProgress } from "./useJobProgress";
import type { LogKind, LogLine, ResultTarget } from "./controllerTypes";
import { cleanString } from "./controllerUtils";
import { appRuntime, runSilent } from "../effect/runtime";
import { jobEventsStream, subscribeJobEvents } from "../effect/jobEvents";
import { createJobProgram } from "../effect/programs/job";

interface JobLifecycleOptions {
  configDrawerOpen: Ref<boolean>;
  isBusy: Ref<boolean>;
  onSourceVideoPath: (filePath: string) => void;
  onSubtitlePath: (filePath: string) => void;
}

export function useJobLifecycle(options: JobLifecycleOptions) {
  const activeJobId = ref("");
  const activeCommand = ref<CommandName | "">("");
  const runStatus = ref("待命");
  const logText = ref("");
  const logBody = ref<HTMLElement | null>(null);
  // 结构化日志行：带到达时间戳与流类型，供产品化日志卡渲染。
  const logLines = ref<LogLine[]>([]);
  const logAutoScroll = ref(true);
  let logLineId = 0;
  const elapsedText = ref("");
  const lastOutputPath = ref("");
  const lastSubtitlePath = ref("");
  const lastEmbeddedVideoPath = ref("");
  const lastLlmTraceDir = ref("");
  const lastSourceVideoPath = ref("");
  const hasSubtitleResult = computed(() => Boolean(lastSubtitlePath.value));
  const hasVideoResult = computed(() => Boolean(lastEmbeddedVideoPath.value));
  const hasTraceResult = computed(() => Boolean(lastLlmTraceDir.value));
  const hasSourceVideoResult = computed(() => Boolean(lastSourceVideoPath.value));
  const hasAnyResult = computed(
    () => hasSubtitleResult.value || hasVideoResult.value || hasTraceResult.value || hasSourceVideoResult.value,
  );
  let eventsFiber: Fiber.RuntimeFiber<void, never> | null = null;
  let elapsedFiber: Fiber.RuntimeFiber<never, never> | null = null;

  const progress = useJobProgress();
  const subtitlePreview = ref<SubtitlePreviewSnapshot>({ revision: 0, entries: [], final: false });

  const program = createJobProgram({
    isBusy: options.isBusy,
    configDrawerOpen: options.configDrawerOpen,
    activeJobId,
    activeCommand,
    runStatus,
    lastOutputPath,
    lastSubtitlePath,
    lastEmbeddedVideoPath,
    lastLlmTraceDir,
    lastSourceVideoPath,
    appendLog,
    onSubtitlePath: options.onSubtitlePath,
    onSourceVideoPath: options.onSourceVideoPath,
    recordProgress: (event) => {
      if (event.stage === "subtitle_preview") {
        subtitlePreview.value = applySubtitlePreview(subtitlePreview.value, event.preview);
      } else {
        progress.recordProgress(event);
      }
    },
    resetProgress: (command) => {
      subtitlePreview.value = { revision: 0, entries: [], final: false };
      activeCommand.value = command;
      progress.resetProgress(command);
    },
    finishProgress: progress.finishJobProgress,
  });

  function appendLog(text: string, kind: LogKind = "stdout"): void {
    const prefix = kind === "stderr" ? "[stderr] " : "";
    logText.value += `${prefix}${text}`;
    const time = new Date().toTimeString().slice(0, 8);
    for (const line of text.replace(/\r?\n$/, "").split(/\r?\n/)) {
      if (!line) {
        continue;
      }
      logLines.value.push({ id: ++logLineId, time, kind, text: line });
    }
    // 长跑任务日志可能很长，保留最近 3000 行防止渲染卡顿
    if (logLines.value.length > 3000) {
      logLines.value.splice(0, logLines.value.length - 3000);
    }
    void scrollLogToEnd();
  }

  async function scrollLogToEnd(): Promise<void> {
    if (!logAutoScroll.value) {
      return;
    }
    await nextTick();
    if (logBody.value) {
      logBody.value.scrollTop = logBody.value.scrollHeight;
    }
  }

  const latestLogLine = computed(() => logLines.value.at(-1)?.text || "");

  function refreshElapsed(): void {
    const startedAt = progress.progressState.value.startedAt;
    if (!startedAt) {
      elapsedText.value = "";
      return;
    }
    const totalSeconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    elapsedText.value = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  async function copyLog(): Promise<void> {
    if (!logText.value) {
      return;
    }
    try {
      await navigator.clipboard.writeText(logText.value);
    } catch {
      // 剪贴板不可用时静默失败（Electron 焦点窗口外可能拒绝）
    }
  }

  function setOutputPath(filePath: unknown): void {
    const cleaned = cleanString(filePath);
    if (!cleaned) {
      return;
    }
    lastOutputPath.value = cleaned;
  }

  function setSubtitlePath(filePath: unknown): void {
    const cleaned = cleanString(filePath);
    if (!cleaned) {
      return;
    }
    lastSubtitlePath.value = cleaned;
    setOutputPath(cleaned);
    options.onSubtitlePath(cleaned);
  }

  async function startJob(request: DesktopJobRequest): Promise<void> {
    await appRuntime.runPromise(program.startJob(request));
  }

  async function cancelJob(): Promise<void> {
    await appRuntime.runPromise(program.cancelJob());
  }

  function clearLog(): void {
    logText.value = "";
    logLines.value = [];
  }

  function resultPath(target: ResultTarget): string {
    if (target === "video") {
      return lastEmbeddedVideoPath.value;
    }
    if (target === "trace") {
      return lastLlmTraceDir.value;
    }
    if (target === "source") {
      return lastSourceVideoPath.value;
    }
    return lastSubtitlePath.value;
  }

  async function openResult(target: ResultTarget): Promise<void> {
    const filePath = resultPath(target);
    if (filePath) {
      await runSilent((bridge) => bridge.openPath(filePath));
    }
  }

  async function showResult(target: ResultTarget): Promise<void> {
    const filePath = resultPath(target);
    if (filePath) {
      await runSilent((bridge) => bridge.showInFolder(filePath));
    }
  }

  async function openOutput(): Promise<void> {
    if (lastOutputPath.value) {
      await runSilent((bridge) => bridge.openPath(lastOutputPath.value));
    }
  }

  async function showOutput(): Promise<void> {
    if (lastOutputPath.value) {
      await runSilent((bridge) => bridge.showInFolder(lastOutputPath.value));
    }
  }

  onMounted(() => {
    // 事件流：退订挂在流的生命周期上，fiber 中断时自动执行。
    eventsFiber = appRuntime.runFork(
      Stream.runForEach(jobEventsStream(subscribeJobEvents), (event) => program.handleEvent(event)),
    );
    elapsedFiber = appRuntime.runFork(
      Effect.forever(
        Effect.sleep("1 second").pipe(
          Effect.tap(() =>
            Effect.sync(() => {
              refreshElapsed();
            }),
          ),
        ),
      ),
    );
  });

  onUnmounted(() => {
    if (eventsFiber) {
      appRuntime.runFork(Fiber.interrupt(eventsFiber));
      eventsFiber = null;
    }
    if (elapsedFiber) {
      appRuntime.runFork(Fiber.interrupt(elapsedFiber));
      elapsedFiber = null;
    }
  });

  return {
    subtitlePreview,
    activeCommand,
    activeJobId,
    appendLog,
    cancelJob,
    chunkStatusLabel: progress.chunkStatusLabel,
    chunkTokenRateText: progress.chunkTokenRateText,
    chunkTooltip: progress.chunkTooltip,
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
    lastOutputPath,
    lastSourceVideoPath,
    lastSubtitlePath,
    latestLogLine,
    logAutoScroll,
    logBody,
    logLines,
    logText,
    openOutput,
    openResult,
    progressChunkLegendItems: progress.progressChunkLegendItems,
    progressChunkSummary: progress.progressChunkSummary,
    progressChunks: progress.progressChunks,
    progressIsEmpty: progress.progressIsEmpty,
    progressLongWaitHint: progress.progressLongWaitHint,
    progressStages: progress.progressStages,
    progressState: progress.progressState,
    progressStatusClass: progress.progressStatusClass,
    progressTokenText: progress.progressTokenText,
    progressWaitText: progress.progressWaitText,
    runStatus,
    selectChunk: progress.selectChunk,
    selectedProgressChunk: progress.selectedProgressChunk,
    setOutputPath,
    setSubtitlePath,
    showOutput,
    showResult,
    startJob,
  };
}
