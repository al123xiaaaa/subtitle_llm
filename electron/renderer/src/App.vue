<script setup lang="ts">
import { watch } from "vue";
import MaterialWorkspace from "./components/MaterialWorkspace.vue";
import ConfigDrawer from "./components/ConfigDrawer.vue";
import OnboardingDialog from "./components/OnboardingDialog.vue";
import RunPanel from "./components/RunPanel.vue";
import SidebarNav from "./components/SidebarNav.vue";
import TaskRecordsPanel from "./components/TaskRecordsPanel.vue";
import { useAppController } from "./composables/useAppController";

const controller = useAppController();
const { formActions, forms, inspectedRecord, isBusy, job, onboarding, providerState, records, shell } = controller;
const { workspacePage } = shell;
watch(isBusy, busy => { if (busy) workspacePage.value = 'jobs'; });
async function resumeWorkspaceTask(taskId: string) {
  await records.refreshTaskRecords();
  const record = records.taskRecords.value.find(item => item.task_id === taskId);
  if (record) { workspacePage.value = 'jobs'; await records.continueTaskRecord(record); }
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
          @create="shell.setActiveTab('translate')"
          @resume="resumeWorkspaceTask"
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
