<script setup lang="ts">
import { toRefs } from "vue";
import type { useAppController } from "../composables/useAppController";

type Controller = ReturnType<typeof useAppController>;

const props = defineProps<{
  isBusy: boolean;
  onboarding: Controller["onboarding"];
}>();

const {
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
</script>

<template>
  <div
    id="onboarding"
    :class="['onboarding', { 'is-hidden': !showOnboarding }]"
    aria-modal="true"
    role="dialog"
  >
    <section class="onboarding-panel">
      <p class="eyebrow">
        首次配置
      </p>
      <h1>先连接一个翻译服务</h1>
      <p>保存 API Key 后就可以开始翻译字幕。也可以使用系统环境变量，App 会自动识别。</p>
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
          <strong>{{ provider.name }}</strong>
          <span>{{ provider.id === "deepseek" ? "推荐" : provider.credential.envKey }}</span>
        </button>
      </div>
      <label>
        <span id="onboardingKeyLabel">{{ onboardingKeyLabel }}</span>
        <input
          id="onboardingApiKey"
          v-model="onboardingApiKey"
          type="password"
          autocomplete="off"
          :disabled="isBusy"
        >
      </label>
      <div class="actions">
        <button
          id="onboardingSave"
          class="primary-button"
          type="button"
          :disabled="isBusy"
          @click="saveOnboardingKey"
        >
          保存并开始
        </button>
        <button
          id="onboardingSettings"
          class="secondary-button"
          type="button"
          :disabled="isBusy"
          @click="openSettingsFromOnboarding"
        >
          打开设置
        </button>
      </div>
    </section>
  </div>
</template>
