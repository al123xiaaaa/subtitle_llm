import { Effect, ManagedRuntime } from "effect";
import { Bridge, BridgeLive } from "./bridge";
import type { BridgeService } from "./bridge";

// 应用级单例运行环境：BridgeLive layer 在此注入，composables 只在此边缘处运行程序。
export const appRuntime = ManagedRuntime.make(BridgeLive);

export function runProgram<A>(effect: Effect.Effect<A, never, Bridge>): Promise<A> {
  return appRuntime.runPromise(effect);
}

// 带环境的单次桥调用在边缘直接跑；错误从 Promise rejection 抛出，交给调用方按现有 try/catch 风格处理。
export function runBridge<E, A>(call: (bridge: BridgeService) => Effect.Effect<A, E, Bridge>): Promise<A> {
  return appRuntime.runPromise(Effect.flatMap(Bridge, call));
}

// 不需要结果与错误处理的桥调用（打开文件、在文件夹中显示等）。
export function runSilent<E, A>(call: (bridge: BridgeService) => Effect.Effect<A, E, Bridge>): Promise<void> {
  return runBridge(call).then(
    () => undefined,
    () => undefined,
  );
}
