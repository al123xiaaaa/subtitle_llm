import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import type { FfmpegStatus } from "../types.js";

export const COMMON_FFMPEG_PATHS = ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"];

interface FfmpegDetectorOptions {
  env?: NodeJS.ProcessEnv;
  fileSystem?: Pick<typeof fs, "existsSync">;
  pathModule?: Pick<typeof path, "isAbsolute">;
  spawnSyncFn?: typeof spawnSync;
}

export function createFfmpegDetector({
  env = process.env,
  fileSystem = fs,
  pathModule = path,
  spawnSyncFn = spawnSync,
}: FfmpegDetectorOptions = {}) {
  let cachedStatus: FfmpegStatus | null = null;

  return function detectFfmpeg(): FfmpegStatus {
    if (cachedStatus) {
      return cachedStatus;
    }

    if (env.SUBTITLE_LLM_DISABLE_FFMPEG_DETECT === "1") {
      cachedStatus = missingStatus();
      return cachedStatus;
    }

    const candidates = [env.SUBTITLE_LLM_FFMPEG, env.FFMPEG_BINARY, "ffmpeg", ...COMMON_FFMPEG_PATHS].filter(
      Boolean,
    ) as string[];

    const seen = new Set<string>();
    for (const candidate of candidates) {
      if (seen.has(candidate)) {
        continue;
      }
      seen.add(candidate);

      if (pathModule.isAbsolute(candidate) && !fileSystem.existsSync(candidate)) {
        continue;
      }

      const result = spawnSyncFn(candidate, ["-version"], {
        encoding: "utf8",
        timeout: 2500,
      });
      if (result.status === 0) {
        const firstLine = String(result.stdout || "").split(/\r?\n/)[0] || "FFmpeg 可用";
        cachedStatus = {
          available: true,
          executable: candidate,
          version: firstLine,
          error: "",
        };
        return cachedStatus;
      }
    }

    cachedStatus = missingStatus();
    return cachedStatus;
  };
}

export function missingStatus(): FfmpegStatus {
  return {
    available: false,
    executable: "",
    version: "",
    error: "未找到 FFmpeg。安装 FFmpeg 后可生成带字幕 MKV。",
  };
}
