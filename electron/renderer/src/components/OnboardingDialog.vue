<script setup lang="ts">
import { toRefs } from "vue";
import { Languages } from "@lucide/vue";
import type { useAppController } from "../composables/useAppController";

type Controller = ReturnType<typeof useAppController>;

const props = defineProps<{
  isBusy: boolean;
  onboarding: Controller["onboarding"];
}>();

const {
  dismissOnboarding,
  onboardingApiKey,
  onboardingKeyLabel,
  openSettingsFromOnboarding,
  saveOnboardingKey,
  selectedOnboardingProviderId,
  selectOnboardingProvider,
  showOnboarding,
  visibleOnboardingProviders,
} = props.onboarding;
const { isBusy } = toRefs(props);

function onBackdropClick(event: MouseEvent): void {
  if (event.target === event.currentTarget) {
    dismissOnboarding();
  }
}

function providerCardHint(provider: { id: string; credential: { envKey: string } }): string {
  if (provider.id === "deepseek") {
    return "推荐";
  }
  if (provider.id === "cliproxy") {
    return "本地代理";
  }
  return provider.credential.envKey;
}
</script>

<template>
  <div
    id="onboarding"
    :class="['onboarding', { 'is-hidden': !showOnboarding }]"
    aria-modal="true"
    role="dialog"
    @click="onBackdropClick"
    @keydown.esc="dismissOnboarding"
  >
    <section class="onboarding-panel">
      <div class="onboarding-hero">
        <div class="empty-hero-mark">
          <Languages />
        </div>
        <h1>欢迎使用 Subtitle LLM</h1>
        <p>先连接一个翻译服务，马上就能开始翻译。API Key 只保存在这台电脑上。</p>
      </div>
      <div
        id="onboardingProviders"
        class="provider-cards"
      >
        <button
          v-for="provider in visibleOnboardingProviders"
          :key="provider.id"
          :class="['provider-card', { 'is-selected': provider.id === selectedOnboardingProviderId }]"
          type="button"
          :data-onboarding-provider="provider.id"
          :disabled="isBusy"
          @click="selectOnboardingProvider(provider.id)"
        >
          <span class="provider-mark">{{ provider.name.slice(0, 1) }}</span>
          <strong>{{ provider.name }}</strong>
          <span>{{ providerCardHint(provider) }}</span>
        </button>
      </div>
      <div class="settings-key-row onboarding-key-row">
        <input
          id="onboardingApiKey"
          v-model="onboardingApiKey"
          type="password"
          autocomplete="off"
          :placeholder="onboardingKeyLabel"
          :disabled="isBusy"
          @keyup.enter="saveOnboardingKey"
        >
        <button
          id="onboardingSave"
          class="primary-button"
          type="button"
          :disabled="isBusy"
          @click="saveOnboardingKey"
        >
          保存并开始
        </button>
      </div>
      <div class="onboarding-foot">
        <button
          id="onboardingSettings"
          class="ghost-button"
          type="button"
          :disabled="isBusy"
          @click="openSettingsFromOnboarding"
        >
          打开设置
        </button>
        <button
          id="onboardingLater"
          class="ghost-button"
          type="button"
          :disabled="isBusy"
          @click="dismissOnboarding"
        >
          稍后再说
        </button>
      </div>
    </section>
  </div>
</template>
