import { Effect } from "effect";
import type { BridgeService } from "../bridge";
import { Bridge } from "../bridge";
import type { BridgeError } from "../errors";
import { cleanString } from "../utils";

// 文件选择流程：bridge 返回 null 表示用户取消；桥错误静默忽略(选择器失败无用户可见出口)。
export function pickPath(
  select: (bridge: BridgeService) => Effect.Effect<string | null, BridgeError>,
  use: (filePath: string) => void,
): Effect.Effect<void, never, Bridge> {
  return Effect.gen(function* () {
    const bridge = yield* Bridge;
    const filePath = yield* select(bridge);
    const cleaned = cleanString(filePath);
    if (cleaned) {
      yield* Effect.sync(() => {
        use(cleaned);
      });
    }
  }).pipe(Effect.catchAll(() => Effect.void));
}
