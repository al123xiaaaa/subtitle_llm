import { Effect, Either } from "effect";
import type { Ref } from "vue";
import type { TranslationTaskSummary } from "../../../../types";
import { Bridge } from "../bridge";
import type { LogKind } from "../../composables/controllerTypes";

export interface TaskRecordsPorts {
  readonly showDeletedTaskRecords: Ref<boolean>;
  readonly taskRecords: Ref<TranslationTaskSummary[]>;
  readonly setStatus: (text: string) => void;
  readonly appendLog: (text: string, kind?: LogKind) => void;
}

export const refreshTaskRecords = (ports: TaskRecordsPorts): Effect.Effect<void, never, Bridge> =>
  Effect.gen(function* () {
    const bridge = yield* Bridge;
    const result = yield* bridge.listTranslationTasks(ports.showDeletedTaskRecords.value).pipe(Effect.either);
    yield* Effect.sync(() => {
      if (Either.isRight(result)) {
        ports.taskRecords.value = result.right;
        ports.setStatus(result.right.length > 0 ? "" : "暂无翻译任务记录");
      } else {
        ports.setStatus(result.left.message);
      }
    });
  });

export const softDeleteTaskRecord = (taskId: string, ports: TaskRecordsPorts): Effect.Effect<void, never, Bridge> =>
  Effect.gen(function* () {
    const bridge = yield* Bridge;
    const result = yield* bridge.softDeleteTranslationTask(taskId).pipe(Effect.either);
    if (Either.isRight(result) && !result.right.ok) {
      yield* Effect.sync(() => {
        ports.appendLog(`${result.right.error || result.right.message || "删除任务记录失败"}\n`, "stderr");
      });
    }
    yield* refreshTaskRecords(ports);
  });

export const restoreTaskRecord = (taskId: string, ports: TaskRecordsPorts): Effect.Effect<void, never, Bridge> =>
  Effect.gen(function* () {
    const bridge = yield* Bridge;
    const result = yield* bridge.restoreTranslationTask(taskId).pipe(Effect.either);
    if (Either.isRight(result) && !result.right.ok) {
      yield* Effect.sync(() => {
        ports.appendLog(`${result.right.error || result.right.message || "恢复任务记录失败"}\n`, "stderr");
      });
    }
    yield* refreshTaskRecords(ports);
  });
