<script setup lang="ts">
import { toRefs } from "vue";
import { KeyRound } from "@lucide/vue";
import type { useAppController } from "../../composables/useAppController";
import type { TaskTab } from "../../composables/controllerTypes";

type Controller = ReturnType<typeof useAppController>;

const props = defineProps<{
  activeTab: TaskTab;
  isBusy: boolean;
  providerState: Controller["providerState"];
}>();

const { activeTab, isBusy } = toRefs(props);
const { apiKeyDrafts, clearSettingsKey, providers, saveSettingsKey, statusPillClass } = props.providerState;

// 每家服务的一句话说明（面向用户，不出现 endpoint 等开发词汇）
const PROVIDER_DESCRIPTIONS: Record<string, string> = {
  deepseek: "DeepSeek 官方服务，中文翻译性价比高",
  gemini: "Google Gemini，多语言与长上下文",
  cliproxy: "本机代理，复用命令行已登录的各家模型",
  openai: "OpenAI 或兼容 OpenAI 协议的端点",
};

function providerDescription(id: string): string {
  return PROVIDER_DESCRIPTIONS[id] || "自定义翻译服务";
}
</script>

<template>
  <section
    :class="['task-panel', { 'is-active': activeTab === 'settings' }]"
    data-panel="settings"
    aria-label="设置"
  >
    <div
      id="settingsCards"
      class="settings-grid"
    >
      <div class="settings-intro">
        <KeyRound />
        <p>API Key 只保存在这台电脑上，用于调用对应的翻译服务，不会上传。</p>
      </div>
      <article
        v-for="provider in providers"
        :key="provider.id"
        class="settings-card"
        :data-provider-card="provider.id"
      >
        <div class="settings-card-heading">
          <div class="settings-provider">
            <span class="provider-mark">{{ provider.name.slice(0, 1) }}</span>
            <div>
              <h3>{{ provider.name }}</h3>
              <p>{{ providerDescription(provider.id) }}</p>
            </div>
          </div>
          <span :class="statusPillClass(provider.credential)">{{ provider.credential.label }}</span>
        </div>
        <div class="settings-key-row">
          <input
            v-model="apiKeyDrafts[provider.id]"
            :data-api-key-input="provider.id"
            type="password"
            autocomplete="off"
            :placeholder="provider.credential.available ? '已保存，粘贴新 Key 可替换' : '粘贴 API Key，回车保存'"
            :disabled="isBusy"
            @keyup.enter="saveSettingsKey(provider.id)"
          >
          <button
            class="primary-button"
            type="button"
            :data-save-key="provider.id"
            :disabled="isBusy"
            @click="saveSettingsKey(provider.id)"
          >
            保存
          </button>
        </div>
        <div class="settings-card-foot">
          <span
            class="env-key"
            :title="`环境变量 ${provider.credential.envKey} 也可提供 Key`"
          >{{ provider.credential.envKey }}</span>
          <button
            v-if="provider.credential.available"
            class="ghost-button danger-ghost"
            type="button"
            :data-clear-key="provider.id"
            :disabled="isBusy"
            @click="clearSettingsKey(provider.id)"
          >
            清除
          </button>
        </div>
      </article>
    </div>
  </section>
</template>
