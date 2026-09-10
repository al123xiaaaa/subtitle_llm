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
          翻译任务记录
        </h2>
        <p>{{ taskRecordsStatus || "最近更新的翻译任务" }}</p>
      </div>
      <div class="task-record-actions">
        <button
          class="secondary-button"
          type="button"
          @click="refreshTaskRecords"
        >
          刷新
        </button>
        <button
          class="secondary-button"
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
            <strong>{{ recordTitle(record.input_display) }}</strong>
            <span>{{ record.target_language }} · {{ record.status }} · {{ formatTime(record.updated_at) }}</span>
          </div>
          <p>{{ record.output_file }}</p>
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
            class="danger-button"
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
