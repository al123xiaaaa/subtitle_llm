import { Effect, Either } from "effect";
import type { Ref } from "vue";
import type { AppState, DesktopPreferences, ProviderModel, ProviderSummary } from "../../../../types";
import { Bridge } from "../bridge";
import type { LogKind } from "../../composables/controllerTypes";
import { cleanString } from "../utils";

export interface ProviderPorts {
  readonly appState: Ref<AppState | null>;
  readonly selectedProviderId: Ref<string>;
  readonly selectedModelId: Ref<string>;
  readonly customModelInput: Ref<string>;
  readonly getSelectedProvider: () => ProviderSummary | null;
  readonly hasDynamicModels: (providerId: string) => boolean;
  readonly storeDynamicModels: (providerId: string, models: ProviderModel[]) => void;
  readonly syncModelSelection: () => void;
  readonly updateAppState: (state: AppState) => void;
  readonly clearApiKeyDraft: (providerId: string) => void;
  readonly clearOnboardingKey: () => void;
  readonly appendLog: (text: string, kind?: LogKind) => void;
}

// 保存当前服务商/模型/自定义模型选择到偏好。
export const persistProviderPreference = (ports: ProviderPorts): Effect.Effect<void, never, Bridge> =>
  Effect.gen(function* () {
    const snapshot = yield* Effect.sync(() => ({
      provider: ports.getSelectedProvider(),
      appState: ports.appState.value,
      modelId: ports.selectedModelId.value,
      customModelId: cleanString(ports.customModelInput.value),
    }));
    if (!snapshot.provider || !snapshot.appState) {
      return;
    }
    const preferences: DesktopPreferences = {
      ...snapshot.appState.preferences,
      lastProviderId: snapshot.provider.id,
      modelsByProvider: {
        ...snapshot.appState.preferences?.modelsByProvider,
        [snapshot.provider.id]: snapshot.modelId,
      },
      customModelsByProvider: {
        ...snapshot.appState.preferences?.customModelsByProvider,
        [snapshot.provider.id]: snapshot.customModelId,
      },
    };
    const bridge = yield* Bridge;
    const saved = yield* bridge.savePreferences(preferences).pipe(Effect.either);
    if (Either.isRight(saved)) {
      yield* Effect.sync(() => {
        ports.updateAppState(saved.right);
      });
    } else {
      yield* Effect.sync(() => {
        ports.appendLog(`${saved.left.message}\n`, "stderr");
      });
    }
  });

export const saveSettingsKey = (providerId: string, apiKey: string, ports: ProviderPorts): Effect.Effect<void, never, Bridge> =>
  Effect.gen(function* () {
    const bridge = yield* Bridge;
    const saved = yield* bridge.saveApiKey(providerId, apiKey).pipe(Effect.either);
    if (Either.isRight(saved)) {
      yield* Effect.sync(() => {
        ports.updateAppState(saved.right);
        ports.clearApiKeyDraft(providerId);
      });
    } else {
      yield* Effect.sync(() => {
        ports.appendLog(`${saved.left.message}\n`, "stderr");
      });
    }
  });

export const clearSettingsKey = (providerId: string, ports: ProviderPorts): Effect.Effect<void, never, Bridge> =>
  Effect.gen(function* () {
    const bridge = yield* Bridge;
    const saved = yield* bridge.clearApiKey(providerId).pipe(Effect.either);
    if (Either.isRight(saved)) {
      yield* Effect.sync(() => {
        ports.updateAppState(saved.right);
      });
    } else {
      yield* Effect.sync(() => {
        ports.appendLog(`${saved.left.message}\n`, "stderr");
      });
    }
  });

export const saveOnboardingKey = (providerId: string, apiKey: string, ports: ProviderPorts): Effect.Effect<void, never, Bridge> =>
  Effect.gen(function* () {
    const bridge = yield* Bridge;
    const saved = yield* bridge.saveApiKey(providerId, apiKey).pipe(Effect.either);
    if (Either.isRight(saved)) {
      yield* Effect.sync(() => {
        ports.updateAppState(saved.right);
        ports.selectedProviderId.value = providerId;
        ports.clearOnboardingKey();
        ports.syncModelSelection();
      });
    } else {
      yield* Effect.sync(() => {
        ports.appendLog(`${saved.left.message}\n`, "stderr");
      });
    }
  });

// 动态模型列表拉取失败时静默回退静态目录（沿用原行为）。
export const loadDynamicModels = (provider: ProviderSummary | null, ports: ProviderPorts): Effect.Effect<void, never, Bridge> => {
  if (!provider?.dynamicModels || ports.hasDynamicModels(provider.id)) {
    return Effect.void;
  }
  return Effect.gen(function* () {
    const bridge = yield* Bridge;
    const models = yield* bridge.fetchProviderModels(provider.id).pipe(Effect.either);
    if (Either.isRight(models) && models.right.length > 0) {
      yield* Effect.sync(() => {
        ports.storeDynamicModels(provider.id, models.right);
        ports.syncModelSelection();
      });
    }
  }).pipe(Effect.catchAll(() => Effect.void));
};
