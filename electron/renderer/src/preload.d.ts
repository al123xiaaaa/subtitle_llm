import type { SubtitleLlmBridge } from "../../types";

declare global {
  interface Window {
    subtitleLLM: SubtitleLlmBridge;
  }
}

export {};
