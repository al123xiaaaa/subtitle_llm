import { Effect, Either } from "effect";
import type { Ref } from "vue";
import type { CliProgressEvent, CommandName, DesktopJobRequest, JobEvent } from "../../../../types";
import { formatProgressLogLine } from "../../../../lib/progressModel";
import { Bridge } from "../bridge";
import type { LogKind } from "../../composables/controllerTypes";
import { cleanString } from "../utils";

// 任务生命周期程序：视图状态(Vue refs/回调)由 composable 注入，
// 这里只负责状态机流转、进度折叠与桥调用；错误全部在程序内落成状态/日志。
export interface JobPorts {
  readonly isBusy: Ref<boolean>;
  readonly configDrawerOpen: Ref<boolean>;
  readonly activeJobId: Ref<string>;
  readonly activeCommand: Ref<CommandName | "">;
  readonly runStatus: Ref<string>;
  readonly lastOutputPath: Ref<string>;
  readonly lastSubtitlePath: Ref<string>;
  readonly lastEmbeddedVideoPath: Ref<string>;
  readonly lastLlmTraceDir: Ref<string>;
  readonly lastSourceVideoPath: Ref<string>;
  readonly appendLog: (text: string, kind?: LogKind) => void;
  readonly onSubtitlePath: (filePath: string) => void;
  readonly onSourceVideoPath: (filePath: string) => void;
  readonly recordProgress: (event: CliProgressEvent) => void;
  readonly resetProgress: (command: CommandName) => void;
  readonly finishProgress: (ok: boolean) => void;
}

export interface JobProgram {
  readonly startJob: (request: DesktopJobRequest) => Effect.Effect<void, never, Bridge>;
  readonly cancelJob: () => Effect.Effect<void, never, Bridge>;
  readonly handleEvent: (event: JobEvent) => Effect.Effect<void, never, Bridge>;
}

export function createJobProgram(ports: JobPorts): JobProgram {
  // 进度日志行去重用的上一阶段名，属程序内部暂态。
  let lastLogStage = "";

  const setBusy = (nextBusy: boolean): Effect.Effect<void> =>
    Effect.sync(() => {
      ports.isBusy.value = nextBusy;
      if (nextBusy) {
        ports.configDrawerOpen.value = false;
      }
      if (!nextBusy) {
        ports.activeJobId.value = "";
      }
    });

  const setStatus = (text: string): Effect.Effect<void> =>
    Effect.sync(() => {
      ports.runStatus.value = text;
    });

  const appendLog = (text: string, kind: LogKind = "stdout"): Effect.Effect<void> =>
    Effect.sync(() => {
      ports.appendLog(text, kind);
    });

  const setSubtitlePath = (filePath: unknown): Effect.Effect<void> =>
    Effect.sync(() => {
      const cleaned = cleanString(filePath);
      if (!cleaned) {
        return;
      }
      ports.lastSubtitlePath.value = cleaned;
      ports.lastOutputPath.value = cleaned;
      ports.onSubtitlePath(cleaned);
    });

  const setSourceVideoPath = (filePath: unknown): Effect.Effect<void> =>
    Effect.sync(() => {
      const cleaned = cleanString(filePath);
      if (!cleaned) {
        return;
      }
      ports.lastSourceVideoPath.value = cleaned;
      ports.onSourceVideoPath(cleaned);
    });

  const setEmbeddedVideoPath = (filePath: unknown): Effect.Effect<void> =>
    Effect.sync(() => {
      const cleaned = cleanString(filePath);
      if (!cleaned) {
        return;
      }
      ports.lastEmbeddedVideoPath.value = cleaned;
      ports.lastOutputPath.value = cleaned;
    });

  const setLlmTraceDir = (filePath: unknown): Effect.Effect<void> =>
    Effect.sync(() => {
      const cleaned = cleanString(filePath);
      if (!cleaned) {
        return;
      }
      ports.lastLlmTraceDir.value = cleaned;
    });

  const startJob = (request: DesktopJobRequest): Effect.Effect<void, never, Bridge> =>
    Effect.gen(function* () {
      yield* setBusy(true);
      yield* setStatus("启动中");
      yield* Effect.sync(() => {
        ports.resetProgress(request.command);
      });
      yield* appendLog(`\n$ subtitle-llm ${request.command}\n`);
      const bridge = yield* Bridge;
      const started = yield* Effect.either(bridge.startJob(request));
      if (Either.isLeft(started)) {
        yield* setBusy(false);
        yield* setStatus("启动失败");
        yield* Effect.sync(() => {
          ports.finishProgress(false);
        });
        yield* appendLog(`${started.left.message}\n`, "stderr");
        return;
      }
      yield* Effect.sync(() => {
        ports.activeJobId.value = started.right.jobId;
        ports.activeCommand.value = request.command;
      });
    });

  const cancelJob = (): Effect.Effect<void, never, Bridge> =>
    Effect.gen(function* () {
      const jobId = ports.activeJobId.value;
      if (!jobId) {
        return;
      }
      const bridge = yield* Bridge;
      const result = yield* Effect.either(bridge.cancelJob(jobId));
      if (Either.isRight(result) && result.right.ok) {
        yield* setStatus("正在取消");
        yield* appendLog("正在取消任务...\n");
      }
    });

  const handleEvent = (event: JobEvent): Effect.Effect<void, never, Bridge> =>
    Effect.gen(function* () {
      if (event.type === "started") {
        yield* setStatus("运行中");
        yield* Effect.sync(() => {
          ports.resetProgress(event.command);
        });
        lastLogStage = "";
        yield* appendLog(`Python: ${event.pythonExecutable}\n工作目录: ${event.cwd}\n`);
        if (event.generatedConfigPath) {
          yield* appendLog(`模型配置: ${event.generatedConfigPath}\n`);
        }
      } else if (event.type === "stdout") {
        yield* appendLog(event.text);
      } else if (event.type === "stderr") {
        yield* appendLog(event.text, "stderr");
      } else if (event.type === "progress") {
        // 结构化进度事件已由主进程 stdoutProtocol 解析完毕，这里只做归约与展示。
        yield* Effect.sync(() => {
          ports.recordProgress(event.event);
        });
        const formatted = formatProgressLogLine(event.event, { previousStage: lastLogStage });
        if (formatted) {
          yield* appendLog(`${formatted.line}\n`);
          lastLogStage = formatted.stage;
        }
      } else if (event.type === "result") {
        yield* setSubtitlePath(event.event.output_file);
        yield* setSourceVideoPath(event.event.source_video_file);
        yield* setEmbeddedVideoPath(event.event.output_video_file || event.event.embedded_video_file);
        yield* setLlmTraceDir(event.event.llm_trace_dir);
      } else if (event.type === "error") {
        yield* setBusy(false);
        yield* setStatus("失败");
        yield* Effect.sync(() => {
          ports.finishProgress(false);
        });
        yield* appendLog(`${event.message}\n`, "stderr");
      } else if (event.type === "finished") {
        yield* setBusy(false);
        yield* setStatus(event.code === 0 ? "完成" : `退出码 ${event.code}`);
        yield* Effect.sync(() => {
          ports.finishProgress(event.code === 0);
        });
        yield* appendLog(`\n任务结束：code=${event.code} signal=${event.signal || "none"}\n`, event.code === 0 ? "stdout" : "stderr");
      }
    });

  return { startJob, cancelJob, handleEvent };
}
