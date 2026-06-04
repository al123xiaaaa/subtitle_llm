const VALID_OUTPUT_FORMATS = new Set([
  "source-first",
  "target-first",
  "target-only",
  "source-only",
  "bilingual",
]);

function cleanString(value) {
  return typeof value === "string" ? value.trim() : "";
}

function required(value, label) {
  const cleaned = cleanString(value);
  if (!cleaned) {
    throw new Error(`${label}不能为空`);
  }
  return cleaned;
}

function addOption(args, flag, value) {
  const cleaned = cleanString(value);
  if (cleaned) {
    args.push(flag, cleaned);
  }
}

function buildTranslateArgs(options = {}) {
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

  return args;
}

function buildDownloadArgs(options = {}) {
  const url = required(options.url, "视频 URL");
  const args = ["main.py", "download", url];
  addOption(args, "--output-dir", options.outputDir || "data/input");
  addOption(args, "--source-language", options.sourceLanguage || "en");
  return args;
}

function buildTranscribeArgs(options = {}) {
  const audio = required(options.audio, "音频文件");
  const output = required(options.output, "输出文件");
  const args = ["main.py", "transcribe", audio, "--output", output];
  addOption(args, "--language", options.language || "English");
  addOption(args, "--config", options.config);
  return args;
}

function buildPythonArgs(request = {}) {
  switch (request.command) {
    case "translate":
      return buildTranslateArgs(request.options);
    case "download":
      return buildDownloadArgs(request.options);
    case "transcribe":
      return buildTranscribeArgs(request.options);
    default:
      throw new Error(`未知命令：${request.command || ""}`);
  }
}

function buildEnv(baseEnv = {}, overrides = {}) {
  const env = {
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

module.exports = {
  VALID_OUTPUT_FORMATS,
  buildEnv,
  buildPythonArgs,
};
