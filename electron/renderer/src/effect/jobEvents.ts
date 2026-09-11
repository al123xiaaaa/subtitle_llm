import { Chunk, Effect, Stream } from "effect";
import type { JobEvent } from "../../../types";

// 把 preload 的订阅式回调包装成 Stream：退订逻辑挂在流的生命周期上，
// 消费被中断(scope 关闭)时自动执行，替代手工 unsubscribe。
export function jobEventsStream(
  subscribe: (callback: (event: JobEvent) => void) => () => void,
): Stream.Stream<JobEvent> {
  return Stream.async<JobEvent>((emit) => {
    const unsubscribe = subscribe((event) => {
      emit(Effect.succeed(Chunk.of(event)));
    });
    return Effect.sync(() => {
      unsubscribe();
    });
  });
}

export function subscribeJobEvents(callback: (event: JobEvent) => void): () => void {
  return window.subtitleLLM.onJobEvent(callback);
}
