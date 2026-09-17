import { Effect, Either, Fiber } from "effect";
import type { Ref } from "vue";
import type { ReusableSubtitleMatch } from "../../../../types";
import { Bridge } from "../bridge";
import { appRuntime } from "../runtime";
import { cleanString, looksLikeUrl } from "../utils";

export interface ReusablePorts {
  readonly reusableSubtitle: Ref<ReusableSubtitleMatch | null>;
  readonly getInput: () => string;
  readonly isBusy: () => boolean;
  readonly getDismissedFor: () => string;
  readonly setDismissedFor: (value: string) => void;
}

export interface ReusableController {
  readonly onInputChanged: (value: string) => void;
  readonly dismiss: () => void;
}

// 输入 URL 防抖查询可复用字幕：每次输入变化中断上一次未完成的查询 fiber，
// 取代旧的 token + setTimeout 手工管理；输入已变或已划过的查询不会写回状态。
export function makeReusableController(ports: ReusablePorts): ReusableController {
  let pending: Fiber.RuntimeFiber<void, never> | null = null;

  const cancelPending = (): void => {
    if (pending) {
      const fiber = pending;
      pending = null;
      appRuntime.runFork(Fiber.interrupt(fiber));
    }
  };

  const query = (url: string): Effect.Effect<void, never, Bridge> =>
    Effect.gen(function* () {
      yield* Effect.sleep("400 millis");
      const bridge = yield* Bridge;
      const match = yield* bridge.findReusableSubtitle(url).pipe(Effect.either);
      yield* Effect.sync(() => {
        // 输入已变、正在运行或用户已忽略：一律丢弃过期匹配，避免旧结果闪回。
        if (cleanString(ports.getInput()) !== url || ports.isBusy() || url === ports.getDismissedFor()) {
          return;
        }
        ports.reusableSubtitle.value = Either.isRight(match) ? match.right : null;
      });
    });

  return {
    onInputChanged(value: string): void {
      cancelPending();
      // 输入一变立刻清掉旧匹配：换 URL 期间不允许对旧结果继续操作。
      ports.reusableSubtitle.value = null;
      ports.setDismissedFor("");
      const url = cleanString(value);
      if (!looksLikeUrl(url) || ports.isBusy()) {
        return;
      }
      pending = appRuntime.runFork(query(url));
    },
    dismiss(): void {
      cancelPending();
      ports.setDismissedFor(cleanString(ports.getInput()));
      ports.reusableSubtitle.value = null;
    },
  };
}
