import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 桌面端配置契约的 TS adapter：desktop-contract.json 是 Electron 与 Python 两侧
// 模型参数默认值、ASR 默认值与 ffmpeg 路径的单一来源（Python 侧一致性由
// tests/test_config_contract.py 锁定）。
//
// 不走 JSON module import：lib 以 bundle:false 转译后，Node ESM 要求
// import attribute，而打包进 main.js 时又是内联——两种场景行为不一致。
// 这里统一用 fs 按候选路径读取。
export interface DesktopContract {
  modelParams: {
    summary: Record<string, string>;
    translation: Record<string, string>;
  };
  providerParamOverrides: Record<string, Record<string, Record<string, string>>>;
  asr: {
    model_name: string;
    punc_model: string | null;
    spk_model: string | null;
    max_single_segment_time: number;
    device: string;
    hub: string;
    trust_remote_code: boolean;
    forced_aligner: string | null;
  };
  ffmpegPaths: string[];
  defaultAsrModel: string;
  asrModels: AsrModelProfile[];
}

export interface AsrModelProfile {
  id: string;
  label: string;
  description: string;
  model_name: string;
  hub: string;
  trust_remote_code: boolean;
  punc_model: string | null;
  spk_model: string | null;
  forced_aligner: string | null;
  max_single_segment_time: number;
  language_style: string;
  generate_kwargs: Record<string, unknown>;
}

function loadContract(): DesktopContract {
  const moduleDir = path.dirname(fileURLToPath(import.meta.url));
  const candidates = [
    // 转译后的独立模块（dist/electron/lib）或打包后的 main.js：构建脚本会把契约镜像到 dist/src/...
    path.resolve(moduleDir, "../../src/subtitle_llm/config/desktop-contract.json"),
    path.resolve(moduleDir, "../src/subtitle_llm/config/desktop-contract.json"),
    // 直跑 electron/ 源码（如 tsx）
    path.resolve(moduleDir, "../../../src/subtitle_llm/config/desktop-contract.json"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return JSON.parse(fs.readFileSync(candidate, "utf8")) as DesktopContract;
    }
  }
  throw new Error(`未找到 desktop-contract.json（尝试过：${candidates.join(", ")}）`);
}

export const desktopContract: DesktopContract = loadContract();
