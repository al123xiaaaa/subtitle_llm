<script setup lang="ts">
import { toRefs } from "vue";
import { X } from "@lucide/vue";
import type { useAppController } from "../composables/useAppController";
import DownloadPanel from "./panels/DownloadPanel.vue";
import SettingsPanel from "./panels/SettingsPanel.vue";
import TranscribePanel from "./panels/TranscribePanel.vue";
import TranslatePanel from "./panels/TranslatePanel.vue";

type Controller = ReturnType<typeof useAppController>;

const props = defineProps<{
  formActions: Controller["formActions"];
  forms: Controller["forms"];
  isBusy: boolean;
  providerState: Controller["providerState"];
  shell: Controller["shell"];
}>();

const { activeTab, activeTaskTab, configDrawerOpen, setActiveTab, taskTabs, toggleConfigDrawer } = props.shell;
const { isBusy } = toRefs(props);
const { formActions, forms, providerState } = props;
</script>

<template>
  <aside
    id="configDrawer"
    :class="['config-drawer', { 'is-collapsed': !configDrawerOpen }]"
    aria-label="任务配置"
  >
    <div class="drawer-rail">
      <button
        v-for="tab in taskTabs"
        :key="tab.id"
        type="button"
        :class="['rail-tab', { 'is-active': activeTab === tab.id }]"
        :title="tab.label"
        :data-rail-tab="tab.id"
        @click="setActiveTab(tab.id)"
      >
        <component :is="tab.icon" />
      </button>
    </div>
    <div class="drawer-content">
      <div class="drawer-heading">
        <div>
          <h2>{{ activeTaskTab.label }}</h2>
          <p>{{ activeTaskTab.subtitle }}</p>
        </div>
        <button
          id="collapseDrawer"
          class="icon-button"
          type="button"
          title="收起配置"
          @click="toggleConfigDrawer"
        >
          <X />
        </button>
      </div>

      <TranslatePanel
        :active-tab="activeTab"
        :form-actions="formActions"
        :forms="forms"
        :is-busy="isBusy"
        :provider-state="providerState"
      />
      <DownloadPanel
        :active-tab="activeTab"
        :form-actions="formActions"
        :forms="forms"
        :is-busy="isBusy"
      />
      <TranscribePanel
        :active-tab="activeTab"
        :form-actions="formActions"
        :forms="forms"
        :is-busy="isBusy"
      />
      <SettingsPanel
        :active-tab="activeTab"
        :is-busy="isBusy"
        :provider-state="providerState"
      />
    </div>
  </aside>
</template>
