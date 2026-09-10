import type { CliProgressEvent, CliResultEvent } from "../types.js";

// Python 端 stdout 协议的唯一解析处（协议词汇定义在
// src/subtitle_llm/pipeline/progress_events.py 与 cli/result_events.py）。
// 按行缓冲：pipe 分片只到达半行时先留在缓冲区，跨 chunk 拼完整后再 JSON.parse，
// 不会把半行当成解析失败丢弃。
const RESULT_EVENT_PREFIX = "SUBTITLE_LLM_RESULT ";
const PROGRESS_EVENT_PREFIX = "SUBTITLE_LLM_PROGRESS ";

export type StdoutProtocolEvent =
  | { kind: "progress"; event: CliProgressEvent }
  | { kind: "result"; event: CliResultEvent }
  | { kind: "log"; text: string }
  | { kind: "parse-error"; text: string };

export interface StdoutProtocolParser {
  push: (chunk: string) => StdoutProtocolEvent[];
  flush: () => StdoutProtocolEvent[];
}

export function createStdoutProtocolParser(): StdoutProtocolParser {
  let buffer = "";

  function classify(line: string, terminated: boolean): StdoutProtocolEvent {
    if (line.startsWith(PROGRESS_EVENT_PREFIX)) {
      return parseStructured(line, PROGRESS_EVENT_PREFIX, "progress", "进度事件");
    }
    if (line.startsWith(RESULT_EVENT_PREFIX)) {
      return parseStructured(line, RESULT_EVENT_PREFIX, "result", "结果事件");
    }
    return { kind: "log", text: terminated ? `${line}\n` : line };
  }

  function push(chunk: string): StdoutProtocolEvent[] {
    buffer += chunk;
    const events: StdoutProtocolEvent[] = [];
    for (;;) {
      const match = /\r?\n/.exec(buffer);
      if (!match) {
        break;
      }
      const line = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      events.push(classify(line, true));
    }
    return coalesceLogs(events);
  }

  function flush(): StdoutProtocolEvent[] {
    if (!buffer) {
      return [];
    }
    const line = buffer;
    buffer = "";
    return [classify(line, false)];
  }

  return { push, flush };
}

function parseStructured(
  line: string,
  prefix: string,
  kind: "progress" | "result",
  label: string,
): StdoutProtocolEvent {
  try {
    const event = JSON.parse(line.slice(prefix.length));
    return kind === "progress"
      ? { kind, event: event as CliProgressEvent }
      : { kind, event: event as CliResultEvent };
  } catch (error) {
    return {
      kind: "parse-error",
      text: `${label}解析失败：${error instanceof Error ? error.message : String(error)}\n`,
    };
  }
}

// 连续的普通日志行合并成一个事件，减少 IPC 次数；结构化事件不参与合并。
function coalesceLogs(events: StdoutProtocolEvent[]): StdoutProtocolEvent[] {
  const merged: StdoutProtocolEvent[] = [];
  for (const event of events) {
    const previous = merged[merged.length - 1];
    if (event.kind === "log" && previous?.kind === "log") {
      previous.text += event.text;
    } else {
      merged.push(event);
    }
  }
  return merged;
}
