<script setup lang="ts">
import { watch } from "vue";
import MaterialWorkspace from "./components/MaterialWorkspace.vue";
import ConfigDrawer from "./components/ConfigDrawer.vue";
import OnboardingDialog from "./components/OnboardingDialog.vue";
import RunPanel from "./components/RunPanel.vue";
import SidebarNav from "./components/SidebarNav.vue";
import TaskRecordsPanel from "./components/TaskRecordsPanel.vue";
import { useAppController } from "./composables/useAppController";
import type { Source } from './composables/workspaceTypes';

const controller = useAppController();
const { formActions, forms, inspectedRecord, isBusy, job, onboarding, providerState, records, shell } = controller;
const { workspacePage } = shell;
watch(isBusy, busy => { if (busy) workspacePage.value = 'jobs'; });
async function resumeWorkspaceTask(taskId: string) {
  await records.refreshTaskRecords();
  const record = records.taskRecords.value.find(item => item.task_id === taskId);
  if (record) { workspacePage.value = 'jobs'; await records.continueTaskRecord(record); }
}
function translateSource(source: Source) {
  formActions.newTranslation();
  Object.assign(forms.translateForm, {input:source.path || source.source_url || '', sourceLanguage:source.language,
    materialId:source.material_id, originUrl:source.source_url, materialInput:source.path || source.source_url || '',
    video:source.source_video || '', embedMkv:Boolean(source.source_video && /\.(mp4|mkv|mov|webm)$/i.test(source.source_video))});
}
function useMaterialTool(kind: 'download' | 'transcribe', source: Source) {
  if (kind === 'download') Object.assign(forms.downloadForm, {url:source.source_url || '', sourceLanguage:source.language});
  else Object.assign(forms.transcribeForm, {audio:source.source_video || '', language:source.language, materialId:source.material_id, materialInput:source.source_video || ''});
  shell.setActiveTab(kind);
}
</script>

<template>
  <OnboardingDialog
    :is-busy="isBusy"
    :onboarding="onboarding"
  />

  <div class="app-shell">
    <SidebarNav
      :provider-state="providerState"
      :shell="shell"
    />

    <div class="workspace">
      <main class="main-workspace">
        <MaterialWorkspace
          v-show="workspacePage === 'materials'"
          @create="formActions.newTranslation"
          @resume="resumeWorkspaceTask"
          @source="translateSource"
          @tool="useMaterialTool"
        />
        <div
          v-show="workspacePage === 'jobs'"
          class="task-workbench"
        >
          <TaskRecordsPanel
            :is-busy="isBusy"
            :records="records"
          />
          <RunPanel
            :job="job"
            :inspected-record="inspectedRecord"
            @close-inspection="records.clearInspectedRecord"
          />
        </div>
      </main>
      <ConfigDrawer
        v-show="workspacePage === 'jobs'"
        :form-actions="formActions"
        :forms="forms"
        :is-busy="isBusy"
        :provider-state="providerState"
        :shell="shell"
      />
    </div>
  </div>
</template>
