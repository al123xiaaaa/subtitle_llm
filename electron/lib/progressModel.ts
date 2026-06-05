import type {
  ChunkProgressStatus,
  CliProgressEvent,
  CliProgressUsage,
  CommandName,
  ProgressStageStatus,
} from "../types.js";

export interface ProgressStageItem {
  id: string;
  label: string;
  status: ProgressStageStatus;
}

export interface ChunkActivityItem {
  index: number;
  total: number;
  status: ChunkProgressStatus;
  detail: string;
  label: string;
  message: string;
  entryStart: number | null;
  entryEnd: number | null;
  entryCount: number | null;
  issueSummary: string;
  reason: string;
  traceId: string;
  provider: string;
  model: string;
  durationMs: number | null;
  usage: Required<CliProgressUsage>;
  updatedAt: number;
}

export interface JobProgressState {
  command: CommandName | "";
  stages: ProgressStageItem[];
  currentStage: string;
  currentDetail: string;
  currentLabel: string;
  currentMessage: string;
  currentStatus: ProgressStageStatus;
  startedAt: number | null;
  lastActivityAt: number | null;
  totalChunks: number;
  chunks: ChunkActivityItem[];
  selectedChunkIndex: number | null;
}

const STAGE_LABELS: Record<string, string> = {
  startup: "启动任务",
  prepare_input: "准备输入",
  prepare_translation: "准备翻译",
  processing_chunks: "处理片段",
  generate_result: "生成结果",
  download: "下载资源",
  transcribe: "音频转写",
  mux: "生成 MKV",
  complete: "完成",
};

const COMMAND_STAGES: Record<CommandName, string[]> = {
  translate: ["startup", "prepare_input", "prepare_translation", "processing_chunks", "generate_result", "complete"],
  download: ["startup", "download", "complete"],
  transcribe: ["startup", "transcribe", "complete"],
  mux: ["mux", "complete"],
};

const EMPTY_USAGE: Required<CliProgressUsage> = {
  prompt_tokens: 0,
  completion_tokens: 0,
  total_tokens: 0,
};

export function createInitialJobProgressState(command: CommandName | "" = "", now = Date.now()): JobProgressState {
  return {
    command,
    stages: createStages(command),
    currentStage: "",
    currentDetail: "",
    currentLabel: "等待任务",
    currentMessage: "还没有运行中的任务",
    currentStatus: "waiting",
    startedAt: command ? now : null,
    lastActivityAt: null,
    totalChunks: 0,
    chunks: [],
    selectedChunkIndex: null,
  };
}

export function startProgress(command: CommandName, now = Date.now()): JobProgressState {
  return {
    ...createInitialJobProgressState(command, now),
    currentStage: "startup",
    currentLabel: "启动任务",
    currentMessage: "正在启动 Python 任务",
    currentStatus: "running",
    lastActivityAt: now,
  };
}

export function finishProgress(state: JobProgressState, ok: boolean, now = Date.now()): JobProgressState {
  const status: ProgressStageStatus = ok ? "done" : "failed";
  return {
    ...state,
    stages: state.stages.map((stage) => {
      if (stage.status === "running" || stage.id === "complete") {
        return { ...stage, status };
      }
      return stage;
    }),
    currentStage: "complete",
    currentDetail: "process_finished",
    currentLabel: ok ? "完成" : "任务失败",
    currentMessage: ok ? "任务已完成" : "任务异常结束，请查看详细日志",
    currentStatus: status,
    lastActivityAt: now,
  };
}

export function applyProgressEvent(
  state: JobProgressState,
  event: CliProgressEvent,
  now = Date.now(),
): JobProgressState {
  const command = event.command || state.command;
  const stageId = cleanString(event.stage);
  const status = normalizeStageStatus(event.status);
  const nextTotalChunks = Math.max(state.totalChunks, positiveInt(event.total_chunks), positiveInt(event.chunk?.total));
  const chunks = updateChunks(state.chunks, event, nextTotalChunks, now);
  const selectedChunkIndex =
    state.selectedChunkIndex && chunks.some((chunk) => chunk.index === state.selectedChunkIndex)
      ? state.selectedChunkIndex
      : chunks[0]?.index || null;

  return {
    ...state,
    command,
    stages: updateStages(command, state.stages, stageId, status),
    currentStage: stageId,
    currentDetail: cleanString(event.detail),
    currentLabel: cleanString(event.label) || labelForStage(stageId),
    currentMessage: cleanString(event.message) || cleanString(event.label) || labelForStage(stageId),
    currentStatus: status,
    startedAt: state.startedAt ?? now,
    lastActivityAt: now,
    totalChunks: nextTotalChunks,
    chunks,
    selectedChunkIndex,
  };
}

export function selectProgressChunk(state: JobProgressState, chunkIndex: number): JobProgressState {
  if (!state.chunks.some((chunk) => chunk.index === chunkIndex)) {
    return state;
  }
  return { ...state, selectedChunkIndex: chunkIndex };
}

export function selectedChunk(state: JobProgressState): ChunkActivityItem | null {
  return state.chunks.find((chunk) => chunk.index === state.selectedChunkIndex) || state.chunks[0] || null;
}

export function chunkSummary(chunks: ChunkActivityItem[]): string {
  if (!chunks.length) {
    return "暂无片段活动";
  }
  const counts = new Map<ChunkProgressStatus, number>();
  for (const chunk of chunks) {
    counts.set(chunk.status, (counts.get(chunk.status) || 0) + 1);
  }
  const parts = [
    ["running", "处理中"],
    ["repairing", "修复中"],
    ["review", "待复核"],
    ["done", "完成"],
    ["warning", "带风险"],
    ["failed", "失败"],
  ]
    .map(([status, label]) => {
      const count = counts.get(status as ChunkProgressStatus) || 0;
      return count > 0 ? `${count} 个${label}` : "";
    })
    .filter(Boolean);
  return `${chunks.length} 个片段${parts.length ? `，${parts.join("，")}` : ""}`;
}

export function chunkTooltip(chunk: ChunkActivityItem): string {
  const range = chunk.entryStart && chunk.entryEnd ? `字幕 ${chunk.entryStart}-${chunk.entryEnd}` : "字幕范围未知";
  const lines = [
    `Chunk ${chunk.index}/${chunk.total}`,
    range,
    `状态：${statusLabel(chunk.status)}`,
    `最近动作：${chunk.message || chunk.label}`,
  ];
  if (chunk.issueSummary) {
    lines.push(`问题：${chunk.issueSummary}`);
  }
  if (chunk.model) {
    lines.push(`模型：${chunk.model}`);
  }
  if (chunk.durationMs) {
    lines.push(`耗时：${(chunk.durationMs / 1000).toFixed(1)}s`);
  }
  if (chunk.traceId) {
    lines.push(`诊断：${chunk.traceId}`);
  }
  return lines.join("\n");
}

export function statusLabel(status: ChunkProgressStatus | ProgressStageStatus): string {
  const labels: Record<string, string> = {
    waiting: "等待",
    running: "处理中",
    review: "待复核",
    repairing: "修复中",
    done: "完成",
    warning: "带风险",
    failed: "失败",
    skipped: "跳过",
  };
  return labels[status] || status;
}

function createStages(command: CommandName | ""): ProgressStageItem[] {
  if (!command) {
    return [];
  }
  return (COMMAND_STAGES[command] || []).map((id) => ({ id, label: labelForStage(id), status: "waiting" }));
}

function updateStages(
  command: CommandName | "",
  currentStages: ProgressStageItem[],
  activeStage: string,
  activeStatus: ProgressStageStatus,
): ProgressStageItem[] {
  const stages = currentStages.length ? currentStages : createStages(command);
  const activeIndex = stages.findIndex((stage) => stage.id === activeStage);
  if (activeIndex < 0) {
    return stages;
  }
  return stages.map((stage, index) => {
    if (index < activeIndex && stage.status !== "failed" && stage.status !== "warning") {
      return stageWithStatus(stage, "done");
    }
    if (index === activeIndex) {
      return stageWithStatus(stage, activeStatus);
    }
    return stage;
  });
}

function updateChunks(
  currentChunks: ChunkActivityItem[],
  event: CliProgressEvent,
  totalChunks: number,
  now: number,
): ChunkActivityItem[] {
  let chunks = currentChunks;
  if (totalChunks > chunks.length) {
    chunks = Array.from({ length: totalChunks }, (_, index) => {
      return chunks[index] || createWaitingChunk(index + 1, totalChunks, now);
    });
  }

  const chunkIndex = positiveInt(event.chunk?.index);
  if (!chunkIndex) {
    return chunks;
  }

  const eventChunk = event.chunk || {};
  return chunks.map((chunk) => {
    if (chunk.index !== chunkIndex) {
      return chunk;
    }
    return {
      index: chunk.index,
      total: positiveInt(eventChunk.total) || totalChunks || chunk.total,
      status: normalizeChunkStatus(eventChunk.status),
      detail: cleanString(eventChunk.detail) || cleanString(event.detail) || chunk.detail,
      label: cleanString(event.label) || chunk.label,
      message: cleanString(event.message) || chunk.message,
      entryStart: nullableInt(eventChunk.entry_start, chunk.entryStart),
      entryEnd: nullableInt(eventChunk.entry_end, chunk.entryEnd),
      entryCount: nullableInt(eventChunk.entry_count, chunk.entryCount),
      issueSummary: cleanString(eventChunk.issue_summary) || chunk.issueSummary,
      reason: cleanString(eventChunk.reason) || chunk.reason,
      traceId: cleanString(event.trace_id) || chunk.traceId,
      provider: cleanString(event.model?.provider) || chunk.provider,
      model: cleanString(event.model?.name) || chunk.model,
      durationMs: nullableInt(event.duration_ms, chunk.durationMs),
      usage: {
        prompt_tokens: Number(event.usage?.prompt_tokens || chunk.usage.prompt_tokens || 0),
        completion_tokens: Number(event.usage?.completion_tokens || chunk.usage.completion_tokens || 0),
        total_tokens: Number(event.usage?.total_tokens || chunk.usage.total_tokens || 0),
      },
      updatedAt: now,
    };
  });
}

function stageWithStatus(stage: ProgressStageItem, status: ProgressStageStatus): ProgressStageItem {
  return {
    id: stage.id,
    label: stage.label,
    status,
  };
}

function createWaitingChunk(index: number, total: number, now: number): ChunkActivityItem {
  return {
    index,
    total,
    status: "waiting",
    detail: "waiting",
    label: "等待处理",
    message: "等待进入处理队列",
    entryStart: null,
    entryEnd: null,
    entryCount: null,
    issueSummary: "",
    reason: "",
    traceId: "",
    provider: "",
    model: "",
    durationMs: null,
    usage: { ...EMPTY_USAGE },
    updatedAt: now,
  };
}

function labelForStage(stage: string): string {
  return STAGE_LABELS[stage] || stage;
}

function normalizeStageStatus(status: unknown): ProgressStageStatus {
  const cleaned = cleanString(status);
  if (["waiting", "running", "done", "warning", "failed", "skipped"].includes(cleaned)) {
    return cleaned as ProgressStageStatus;
  }
  return "running";
}

function normalizeChunkStatus(status: unknown): ChunkProgressStatus {
  const cleaned = cleanString(status);
  if (["waiting", "running", "review", "repairing", "done", "warning", "failed", "skipped"].includes(cleaned)) {
    return cleaned as ChunkProgressStatus;
  }
  return "running";
}

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function positiveInt(value: unknown): number {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 0;
}

function nullableInt(value: unknown, fallback: number | null): number | null {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.floor(parsed) : fallback;
}
