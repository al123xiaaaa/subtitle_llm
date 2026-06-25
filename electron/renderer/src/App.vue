<script setup lang="ts">
import ConfigDrawer from "./components/ConfigDrawer.vue";
import OnboardingDialog from "./components/OnboardingDialog.vue";
import RunPanel from "./components/RunPanel.vue";
import SidebarNav from "./components/SidebarNav.vue";
import TaskRecordsPanel from "./components/TaskRecordsPanel.vue";
import { useAppController } from "./composables/useAppController";

const controller = useAppController();
const { formActions, forms, isBusy, job, onboarding, providerState, records, shell } = controller;
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
        <RunPanel :job="job" />
        <TaskRecordsPanel
          :is-busy="isBusy"
          :records="records"
        />
      </main>
      <ConfigDrawer
        :form-actions="formActions"
        :forms="forms"
        :is-busy="isBusy"
        :provider-state="providerState"
        :shell="shell"
      />
    </div>
  </div>
</template>
