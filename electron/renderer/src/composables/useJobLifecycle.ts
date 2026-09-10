import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import type { Ref } from "vue";
import type { CliResultEvent, CommandName, DesktopJobRequest, JobEvent } from "../../../types";
import { formatProgressLogLine } from "../../../lib/progressModel";
import { useJobProgress } from "./useJobProgress";
import type { ResultTarget } from "./controllerTypes";
import { cleanString } from "./controllerUtils";

interface JobLifecycleOptions {
  configDrawerOpen: Ref<boolean>;
  isBusy: Ref<boolean>;
  onSourceVideoPath: (filePath: string) => void;
  onSubtitlePath: (filePath: string) => void;
}

interface LogLine {
  id: number;
  time: string;
  kind: "stdout" | "stderr";
  text: string;
}

export function useJobLifecycle(api: Window["subtitleLLM"], options: JobLifecycleOptions) {
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
  let elapsedTimer: ReturnType<typeof setInterval> | null = null;
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
  let lastLogStage = "";
  let unsubscribeJobEvents: (() => void) | null = null;

  const progress = useJobProgress();

  function setBusy(nextBusy: boolean): void {
    options.isBusy.value = nextBusy;
    if (nextBusy) {
      options.configDrawerOpen.value = false;
    }
    if (!nextBusy) {
      activeJobId.value = "";
    }
  }

  function appendLog(text: string, kind: "stdout" | "stderr" = "stdout"): void {
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

  function setStatus(text: string): void {
    runStatus.value = text;
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

  function setSourceVideoPath(filePath: unknown): void {
    const cleaned = cleanString(filePath);
    if (!cleaned) {
      return;
    }
    lastSourceVideoPath.value = cleaned;
    options.onSourceVideoPath(cleaned);
  }

  function setEmbeddedVideoPath(filePath: unknown): void {
    const cleaned = cleanString(filePath);
    if (!cleaned) {
      return;
    }
    lastEmbeddedVideoPath.value = cleaned;
    setOutputPath(cleaned);
  }

  function setLlmTraceDir(filePath: unknown): void {
    const cleaned = cleanString(filePath);
    if (!cleaned) {
      return;
    }
    lastLlmTraceDir.value = cleaned;
  }

  function applyResultEvent(event: CliResultEvent): void {
    setSubtitlePath(event.output_file);
    setSourceVideoPath(event.source_video_file);
    setEmbeddedVideoPath(event.output_video_file || event.embedded_video_file);
    setLlmTraceDir(event.llm_trace_dir);
  }

  async function startJob(request: DesktopJobRequest): Promise<void> {
    try {
      setBusy(true);
      setStatus("启动中");
      progress.resetProgress(request.command);
      appendLog(`\n$ subtitle-llm ${request.command}\n`);
      const response = await api.startJob(request);
      activeJobId.value = response.jobId;
      activeCommand.value = request.command;
    } catch (error) {
      setBusy(false);
      setStatus("启动失败");
      progress.finishJobProgress(false);
      appendLog(`${error instanceof Error ? error.message : String(error)}\n`, "stderr");
    }
  }

  async function cancelJob(): Promise<void> {
    if (!activeJobId.value) {
      return;
    }
    const result = await api.cancelJob(activeJobId.value);
    if (result.ok) {
      setStatus("正在取消");
      appendLog("正在取消任务...\n");
    }
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
      await api.openPath(filePath);
    }
  }

  async function showResult(target: ResultTarget): Promise<void> {
    const filePath = resultPath(target);
    if (filePath) {
      await api.showInFolder(filePath);
    }
  }

  async function openOutput(): Promise<void> {
    if (lastOutputPath.value) {
      await api.openPath(lastOutputPath.value);
    }
  }

  async function showOutput(): Promise<void> {
    if (lastOutputPath.value) {
      await api.showInFolder(lastOutputPath.value);
    }
  }

  function handleJobEvent(event: JobEvent): void {
    if (event.type === "started") {
      setStatus("运行中");
      progress.resetProgress(event.command);
      lastLogStage = "";
      appendLog(`Python: ${event.pythonExecutable}\n工作目录: ${event.cwd}\n`);
      if (event.generatedConfigPath) {
        appendLog(`模型配置: ${event.generatedConfigPath}\n`);
      }
    } else if (event.type === "stdout") {
      appendLog(event.text);
    } else if (event.type === "stderr") {
      appendLog(event.text, "stderr");
    } else if (event.type === "progress") {
      // 结构化进度事件已由主进程 stdoutProtocol 解析完毕，这里只做归约与展示。
      progress.recordProgress(event.event);
      const formatted = formatProgressLogLine(event.event, { previousStage: lastLogStage });
      if (formatted) {
        appendLog(`${formatted.line}\n`);
        lastLogStage = formatted.stage;
      }
    } else if (event.type === "result") {
      applyResultEvent(event.event);
    } else if (event.type === "error") {
      setBusy(false);
      setStatus("失败");
      progress.finishJobProgress(false);
      appendLog(`${event.message}\n`, "stderr");
    } else if (event.type === "finished") {
      setBusy(false);
      setStatus(event.code === 0 ? "完成" : `退出码 ${event.code}`);
      progress.finishJobProgress(event.code === 0);
      appendLog(`\n任务结束：code=${event.code} signal=${event.signal || "none"}\n`, event.code === 0 ? "stdout" : "stderr");
    }
  }

  onMounted(() => {
    unsubscribeJobEvents = api.onJobEvent(handleJobEvent);
    elapsedTimer = setInterval(refreshElapsed, 1000);
  });

  onUnmounted(() => {
    unsubscribeJobEvents?.();
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
    }
  });

  return {
    activeCommand,
    activeJobId,
    appendLog,
    cancelJob,
    chunkStatusLabel: progress.chunkStatusLabel,
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
