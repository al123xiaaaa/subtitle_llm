const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const COMMON_FFMPEG_PATHS = [
  "/opt/homebrew/bin/ffmpeg",
  "/usr/local/bin/ffmpeg",
  "/usr/bin/ffmpeg",
];

function createFfmpegDetector({
  env = process.env,
  fileSystem = fs,
  pathModule = path,
  spawnSyncFn = spawnSync,
} = {}) {
  let cachedStatus = null;

  return function detectFfmpeg() {
    if (cachedStatus) {
      return cachedStatus;
    }

    if (env.SUBTITLE_LLM_DISABLE_FFMPEG_DETECT === "1") {
      cachedStatus = missingStatus();
      return cachedStatus;
    }

    const candidates = [
      env.SUBTITLE_LLM_FFMPEG,
      env.FFMPEG_BINARY,
      "ffmpeg",
      ...COMMON_FFMPEG_PATHS,
    ].filter(Boolean);

    const seen = new Set();
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

function missingStatus() {
  return {
    available: false,
    executable: "",
    version: "",
    error: "未找到 FFmpeg。安装 FFmpeg 后可生成带字幕 MKV。",
  };
}

module.exports = {
  COMMON_FFMPEG_PATHS,
  createFfmpegDetector,
  missingStatus,
};
