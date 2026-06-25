import { computed, ref } from "vue";
import { AudioLines, Download, Languages, Settings as SettingsIcon } from "@lucide/vue";
import type { Ref } from "vue";
import type { TaskTab, TaskTabMeta } from "./controllerTypes";

export function useAppShell(isBusy: Ref<boolean>) {
  const activeTab = ref<TaskTab>("translate");
  const configDrawerOpen = ref(true);
  const taskTabs: TaskTabMeta[] = [
    { id: "translate", label: "翻译字幕", subtitle: "SRT、转写 JSON 或视频 URL", icon: Languages },
    { id: "download", label: "下载字幕", subtitle: "视频地址与字幕语言", icon: Download },
    { id: "transcribe", label: "音频转写", subtitle: "生成 SRT 后可继续翻译", icon: AudioLines },
    { id: "settings", label: "设置", subtitle: "管理翻译服务 API Key", icon: SettingsIcon },
  ];
  const activeTaskTab = computed(() => taskTabs.find((tab) => tab.id === activeTab.value) || taskTabs[0]);

  function setActiveTab(tab: TaskTab): void {
    activeTab.value = tab;
    if (!isBusy.value) {
      configDrawerOpen.value = true;
    }
  }

  function toggleConfigDrawer(): void {
    configDrawerOpen.value = !configDrawerOpen.value;
  }

  function collapseConfigDrawer(): void {
    configDrawerOpen.value = false;
  }

  return {
    activeTab,
    activeTaskTab,
    collapseConfigDrawer,
    configDrawerOpen,
    setActiveTab,
    taskTabs,
    toggleConfigDrawer,
  };
}
