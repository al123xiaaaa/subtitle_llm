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
        if (cleanString(ports.getInput()) !== url) {
          return;
        }
        if (Either.isRight(match)) {
          ports.reusableSubtitle.value = match.right && url !== ports.getDismissedFor() ? match.right : null;
        } else {
          ports.reusableSubtitle.value = null;
        }
      });
    });

  return {
    onInputChanged(value: string): void {
      cancelPending();
      const url = cleanString(value);
      if (!looksLikeUrl(url) || ports.isBusy()) {
        ports.reusableSubtitle.value = null;
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
