import { Data } from "effect";

// IPC 桥调用失败：统一在程序内 catchTags/catchAll 落成日志或静默回退。
export class BridgeError extends Data.TaggedError("BridgeError")<{
  readonly method: string;
  readonly message: string;
}> {}
