<script setup lang="ts">
import { toRefs } from "vue";
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
      <article
        v-for="provider in providers"
        :key="provider.id"
        class="settings-card"
        :data-provider-card="provider.id"
      >
        <div class="settings-card-heading">
          <div>
            <h3>{{ provider.name }}</h3>
            <p>{{ provider.credential.envKey }}</p>
          </div>
          <span :class="statusPillClass(provider.credential)">{{ provider.credential.label }}</span>
        </div>
        <label>
          <span>{{ provider.name }} API Key</span>
          <input
            v-model="apiKeyDrafts[provider.id]"
            :data-api-key-input="provider.id"
            type="password"
            autocomplete="off"
            placeholder="粘贴新的 API Key"
            :disabled="isBusy"
          >
        </label>
        <div class="card-actions">
          <button
            class="primary-button"
            type="button"
            :data-save-key="provider.id"
            :disabled="isBusy"
            @click="saveSettingsKey(provider.id)"
          >
            保存
          </button>
          <button
            class="secondary-button"
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
