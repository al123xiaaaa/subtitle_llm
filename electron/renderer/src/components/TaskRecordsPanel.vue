<script setup lang="ts">
import { ref } from "vue";
import type { useAppController } from "../composables/useAppController";

type Controller = ReturnType<typeof useAppController>;

const props = defineProps<{
  isBusy: boolean;
  records: Controller["records"];
}>();

const {
  continueTaskRecord,
  inspectTaskRecord,
  openTaskOutput,
  openTaskSourceVideo,
  refreshTaskRecords,
  restoreTaskRecord,
  showDeletedTaskRecords,
  showTaskOutput,
  showTaskSourceVideo,
  softDeleteTaskRecord,
  taskRecords,
  taskRecordsStatus,
  toggleDeletedTaskRecords,
} = props.records;

// 头部"⋯"菜单：收拢刷新/已删除两个低频操作，把标题留给内容。
const menuOpen = ref(false);
const menuRoot = ref<HTMLElement | null>(null);
function toggleMenu(): void {
  menuOpen.value = !menuOpen.value;
}
function onMenuRefresh(): void {
  menuOpen.value = false;
  refreshTaskRecords();
}
function onMenuToggleDeleted(): void {
  menuOpen.value = false;
  toggleDeletedTaskRecords();
}

function recordTitle(input: string): string {
  const parts = input.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || input || "未命名任务";
}

// URL 来源的任务用输出文件名做标题（去掉语言后缀与扩展名），不再展示 raw URL。
function displayTitle(record: { input_display: string; output_file: string }): string {
  if (/^https?:\/\//.test(record.input_display) && record.output_file) {
    const fileName = recordTitle(record.output_file);
    return fileName.replace(/\.[a-z-]+\.srt$/i, "").replace(/\.srt$/i, "") || fileName;
  }
  return recordTitle(record.input_display);
}

const STATUS_LABELS: Record<string, string> = {  created: "已创建",
  preparing_input: "准备输入",
  preparing_translation: "准备翻译",
  processing_chunks: "翻译中",
  finalizing_output: "生成结果",
  completed: "已完成",
  completed_with_warnings: "有警告",
  failed: "失败",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] || status;
}

type StatusTone = "success" | "warning" | "danger" | "info" | "muted";

const STATUS_TONES: Record<string, StatusTone> = {
  created: "info",
  preparing_input: "info",
  preparing_translation: "info",
  processing_chunks: "info",
  finalizing_output: "info",
  completed: "success",
  completed_with_warnings: "warning",
  failed: "danger",
};

function statusTone(status: string): StatusTone {
  return STATUS_TONES[status] || "muted";
}

function relativeTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const elapsed = Date.now() - date.getTime();
  const minutes = Math.round(elapsed / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} 天前`;
  return date.toLocaleDateString();
}

function canContinue(status: string, deletedAt?: string | null): boolean {
  return !deletedAt && ["created", "running", "waiting_review", "failed"].includes(status);
}
</script>

<template>
  <section
    id="taskRecordsPanel"
    class="task-records-panel"
    aria-labelledby="taskRecordsTitle"
  >
    <div class="task-records-heading">
      <div>
        <h2 id="taskRecordsTitle">
          最近任务
        </h2>
        <!-- 状态承载加载/错误信息，任何宽度都要保持可见；描述文案可在窄布局隐藏。 -->
        <p
          v-if="taskRecordsStatus"
          class="task-records-status"
        >
          {{ taskRecordsStatus }}
        </p>
        <p
          v-else
          class="task-records-hint"
        >
          继续或回看之前的翻译
        </p>
      </div>
      <div
        ref="menuRoot"
        class="task-record-menu"
      >
        <button
          class="ghost-button"
          type="button"
          aria-label="更多任务操作"
          :aria-expanded="menuOpen"
          @click="toggleMenu"
        >
          ⋯
        </button>
        <div
          v-if="menuOpen"
          class="task-record-menu-list"
          role="menu"
        >
          <button
            role="menuitem"
            type="button"
            @click="onMenuRefresh"
          >
            刷新
          </button>
          <button
            role="menuitem"
            type="button"
            @click="onMenuToggleDeleted"
          >
            {{ showDeletedTaskRecords ? "隐藏已删除" : "查看已删除" }}
          </button>
        </div>
      </div>
    </div>

    <div class="task-record-list">
      <p
        v-if="taskRecords.length === 0"
        class="task-record-empty"
      >
        {{ showDeletedTaskRecords ? "暂无已删除任务" : "暂无历史任务，完成翻译后会显示在这里。" }}
      </p>
      <article
        v-for="record in taskRecords"
        :key="record.task_id"
        :class="['task-record-item', { 'is-deleted': record.deleted_at, 'is-inspectable': !isBusy }]"
        @click="inspectTaskRecord(record)"
      >
        <button
          class="task-record-main task-record-inspect"
          type="button"
          :disabled="isBusy"
          :aria-label="`查看任务：${displayTitle(record)}`"
          @click.stop="inspectTaskRecord(record)"
        >
          <div class="task-record-title-row">
            <strong :title="displayTitle(record)">{{ displayTitle(record) }}</strong>
            <span :class="['status-badge', `is-${statusTone(record.status)}`]">{{ statusLabel(record.status) }}</span>
          </div>
          <span
            class="task-record-meta"
            :title="`${record.target_language} · ${statusLabel(record.status)} · ${new Date(record.updated_at).toLocaleString()}`"
          >{{ record.target_language }} · {{ relativeTime(record.updated_at) }}</span>
        </button>
        <div
          class="task-record-buttons"
          @click.stop
        >
          <button
            v-if="canContinue(record.status, record.deleted_at)"
            class="primary-button"
            type="button"
            :disabled="isBusy"
            @click="continueTaskRecord(record)"
          >
            继续
          </button>
          <button
            v-if="record.deleted_at"
            class="secondary-button"
            type="button"
            @click="restoreTaskRecord(record)"
          >
            恢复
          </button>
          <details class="task-record-more">
            <summary
              class="ghost-button"
              aria-label="更多操作"
            >
              ⋯
            </summary>
            <div
              class="task-record-more-list"
              role="menu"
            >
              <template v-if="!record.deleted_at">
                <button
                  role="menuitem"
                  type="button"
                  @click="openTaskOutput(record)"
                >
                  打开字幕
                </button>
                <button
                  role="menuitem"
                  type="button"
                  @click="showTaskOutput(record)"
                >
                  定位字幕
                </button>
                <template v-if="record.source_video_file">
                  <button
                    role="menuitem"
                    type="button"
                    @click="openTaskSourceVideo(record)"
                  >
                    打开源视频
                  </button>
                  <button
                    role="menuitem"
                    type="button"
                    @click="showTaskSourceVideo(record)"
                  >
                    定位源视频
                  </button>
                </template>
              </template>
              <button
                role="menuitem"
                type="button"
                class="danger-ghost"
                @click="softDeleteTaskRecord(record)"
              >
                删除
              </button>
            </div>
          </details>
        </div>
      </article>
    </div>
  </section>
</template>
