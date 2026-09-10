<script setup lang="ts">
import type { useAppController } from "../composables/useAppController";

type Controller = ReturnType<typeof useAppController>;

const props = defineProps<{
  isBusy: boolean;
  records: Controller["records"];
}>();

const {
  continueTaskRecord,
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
  completed_with_warnings: "已完成 · 有警告",
  failed: "失败",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] || status;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
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
        <p>{{ taskRecordsStatus || "继续或回看之前的翻译" }}</p>
      </div>
      <div class="task-record-actions">
        <button
          class="ghost-button"
          type="button"
          @click="refreshTaskRecords"
        >
          刷新
        </button>
        <button
          class="ghost-button"
          type="button"
          @click="toggleDeletedTaskRecords"
        >
          {{ showDeletedTaskRecords ? "隐藏已删除" : "查看已删除" }}
        </button>
      </div>
    </div>

    <div class="task-record-list">
      <article
        v-for="record in taskRecords"
        :key="record.task_id"
        :class="['task-record-item', { 'is-deleted': record.deleted_at }]"
      >
        <div class="task-record-main">
          <div>
            <strong>{{ displayTitle(record) }}</strong>
            <span>{{ record.target_language }} · {{ statusLabel(record.status) }} · {{ formatTime(record.updated_at) }}</span>
          </div>
          <div class="task-record-files">
            <span class="task-file-actions">
              <span class="task-file-label">字幕</span>
              <button
                class="ghost-button"
                type="button"
                @click="openTaskOutput(record)"
              >
                打开
              </button>
              <button
                class="ghost-button"
                type="button"
                @click="showTaskOutput(record)"
              >
                定位
              </button>
            </span>
            <span
              v-if="record.source_video_file"
              class="task-file-actions"
            >
              <span class="task-file-label">源视频</span>
              <button
                class="ghost-button"
                type="button"
                @click="openTaskSourceVideo(record)"
              >
                打开
              </button>
              <button
                class="ghost-button"
                type="button"
                @click="showTaskSourceVideo(record)"
              >
                定位
              </button>
            </span>
          </div>
        </div>
        <div class="task-record-buttons">
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
          <button
            v-else
            class="ghost-button danger-ghost"
            type="button"
            @click="softDeleteTaskRecord(record)"
          >
            删除
          </button>
        </div>
      </article>
    </div>
  </section>
</template>
