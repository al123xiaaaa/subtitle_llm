import type {
  DesktopJobRequest,
  DownloadJobOptions,
  MuxJobOptions,
  TranscribeJobOptions,
  TranslateJobOptions,
} from "../types.js";

export const VALID_OUTPUT_FORMATS = new Set(["source-first", "target-first", "target-only", "source-only", "bilingual"]);

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function required(value: unknown, label: string): string {
  const cleaned = cleanString(value);
  if (!cleaned) {
    throw new Error(`${label}不能为空`);
  }
  return cleaned;
}

function addOption(args: string[], flag: string, value: unknown): void {
  const cleaned = cleanString(value);
  if (cleaned) {
    args.push(flag, cleaned);
  }
}

function buildTranslateArgs(options: TranslateJobOptions): string[] {
  const input = required(options.input, "输入文件或 URL");
  const targetLanguage = required(options.targetLanguage, "目标语言");
  const args = ["main.py", "translate", "--input", input, "--target-language", targetLanguage];

  addOption(args, "--output", options.output);
  addOption(args, "--config", options.config);
  addOption(args, "--source-language", options.sourceLanguage || "en");

  const outputFormat = cleanString(options.outputFormat);
  if (outputFormat) {
    if (!VALID_OUTPUT_FORMATS.has(outputFormat)) {
      throw new Error(`不支持的输出格式：${outputFormat}`);
    }
    args.push("--format", outputFormat);
  }

  if (options.resume) {
    args.push("--resume");
  }

  if (options.reviewMode === "tui") {
    args.push("--review");
  } else if (options.reviewMode === "auto") {
    args.push("--no-review");
  }

  if (options.embedVideo) {
    args.push("--embed-video");
    addOption(args, "--video", options.video);
    addOption(args, "--video-output", options.videoOutput);
    addOption(args, "--ffmpeg", options.ffmpeg);
  }

  return args;
}

function buildDownloadArgs(options: DownloadJobOptions): string[] {
  const url = required(options.url, "视频 URL");
  const args = ["main.py", "download", url];
  addOption(args, "--output-dir", options.outputDir || "data/input");
  addOption(args, "--source-language", options.sourceLanguage || "en");
  return args;
}

function buildTranscribeArgs(options: TranscribeJobOptions): string[] {
  const audio = required(options.audio, "音频文件");
  const output = required(options.output, "输出文件");
  const args = ["main.py", "transcribe", audio, "--output", output];
  addOption(args, "--language", options.language || "English");
  addOption(args, "--config", options.config);
  return args;
}

function buildMuxArgs(options: MuxJobOptions): string[] {
  const video = required(options.video, "视频文件");
  const subtitle = required(options.subtitle, "字幕文件");
  const args = ["main.py", "mux", video, subtitle];
  addOption(args, "--output", options.output);
  addOption(args, "--target-language", options.targetLanguage || "Chinese");
  addOption(args, "--ffmpeg", options.ffmpeg);
  return args;
}

export function buildPythonArgs(request: DesktopJobRequest): string[] {
  switch (request.command) {
    case "translate":
      return buildTranslateArgs(request.options);
    case "download":
      return buildDownloadArgs(request.options);
    case "transcribe":
      return buildTranscribeArgs(request.options);
    case "mux":
      return buildMuxArgs(request.options);
    default:
      throw new Error("未知命令");
  }
}

export function buildEnv(
  baseEnv: NodeJS.ProcessEnv = {},
  overrides: Record<string, string | undefined> = {},
): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {
    ...baseEnv,
    PYTHONIOENCODING: "utf-8",
    PYTHONUNBUFFERED: "1",
  };

  for (const [rawName, rawValue] of Object.entries(overrides || {})) {
    const name = cleanString(rawName);
    const value = cleanString(rawValue);
    if (!name || !value) {
      continue;
    }
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
      throw new Error(`环境变量名无效：${name}`);
    }
    env[name] = value;
  }

  return env;
}
