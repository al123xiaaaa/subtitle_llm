<script setup lang="ts">
import type { useAppController } from "../composables/useAppController";

type Controller = ReturnType<typeof useAppController>;

const props = defineProps<{
  providerState: Controller["providerState"];
  shell: Controller["shell"];
}>();

const { activeTab, setActiveTab, taskTabs } = props.shell;
const { appState, providers, runtimeInfo, statusPillClass } = props.providerState;
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-mark">
        SL
      </div>
      <div>
        <h1>Subtitle LLM</h1>
        <p
          id="runtimeInfo"
          :title="appState?.pythonExecutable || ''"
        >
          <span :class="['runtime-dot', { 'is-error': appState && !appState.hasMainPy }]" />
          {{ runtimeInfo }}
        </p>
      </div>
    </div>
    <nav
      class="tabs"
      aria-label="任务类型"
    >
      <button
        v-for="tab in taskTabs"
        :key="tab.id"
        :class="['tab', { 'is-active': activeTab === tab.id }]"
        type="button"
        :data-tab="tab.id"
        @click="setActiveTab(tab.id)"
      >
        <component :is="tab.icon" />
        {{ tab.label }}
      </button>
    </nav>
    <section
      class="status-panel"
      aria-labelledby="statusTitle"
    >
      <h2 id="statusTitle">
        翻译服务
      </h2>
      <div
        id="sidebarProviderStatus"
        class="provider-status-list"
      >
        <div
          v-for="provider in providers"
          :key="provider.id"
          class="provider-status-item"
        >
          <span>{{ provider.name }}</span>
          <span :class="statusPillClass(provider.credential)">{{ provider.credential.label }}</span>
        </div>
      </div>
    </section>
  </aside>
</template>
