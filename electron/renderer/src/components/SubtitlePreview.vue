<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { SubtitlePreviewSnapshot } from "../../../types";

const props = defineProps<{ snapshot: SubtitlePreviewSnapshot; running: boolean }>();
const page = ref(0);
const pageSize = 50;
const pages = computed(() => Math.max(1, Math.ceil(props.snapshot.entries.length / pageSize)));
const entries = computed(() => props.snapshot.entries.slice(page.value * pageSize, (page.value + 1) * pageSize));
watch(() => props.snapshot.revision, (revision, previous) => {
  if (revision < previous) page.value = 0;
  page.value = Math.min(page.value, pages.value - 1);
});
function timestamp(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  return [Math.floor(total / 3600), Math.floor(total / 60) % 60, total % 60]
    .map((value) => String(value).padStart(2, "0")).join(":");
}
</script>

<template>
  <section
    id="subtitlePreview"
    class="subtitle-preview"
    aria-labelledby="subtitlePreviewTitle"
  >
    <header class="subtitle-preview-heading">
      <div>
        <h3 id="subtitlePreviewTitle">
          双语字幕预览
        </h3>
        <p id="subtitlePreviewCount">
          {{ snapshot.entries.length }} 条 · {{ snapshot.final ? "最终字幕" : running ? "随已确认片段更新" : "已保留的片段" }}
        </p>
      </div>
      <span class="status-pill">{{ snapshot.final ? "已完成" : running ? "翻译中" : "未完成" }}</span>
    </header>
    <div
      v-if="!entries.length"
      class="subtitle-preview-empty"
      data-pending
    >
      <strong>{{ running ? "等待第一批已确认字幕" : "暂无可预览字幕" }}</strong>
      <p>{{ running ? "片段完成翻译与检查后，原文和译文会显示在这里。" : "已确认的字幕会保留在这里；可查看运行日志了解任务状态。" }}</p>
    </div>
    <div
      v-else
      class="subtitle-preview-list"
    >
      <article
        v-for="(entry, index) in entries"
        :key="`${snapshot.revision}-${index}`"
        :class="['subtitle-preview-row', { 'subtitle-preview-warn': entry.needs_retranslation }]"
      >
        <div class="subtitle-preview-time">
          <span>{{ page * pageSize + index + 1 }} · {{ timestamp(entry.start) }} – {{ timestamp(entry.end) }}</span>
          <span v-if="entry.needs_retranslation">需复核</span>
        </div>
        <p class="subtitle-preview-original">
          {{ entry.original_text }}
        </p>
        <p class="subtitle-preview-translation">
          {{ entry.translated_text || "暂无译文（保留原文）" }}
        </p>
      </article>
    </div>
    <footer
      v-if="pages > 1"
      class="subtitle-preview-pagination"
    >
      <button
        class="secondary-button"
        type="button"
        :disabled="page === 0"
        @click="page--"
      >
        上一页
      </button>
      <span>{{ page + 1 }} / {{ pages }}</span>
      <button
        class="secondary-button"
        type="button"
        :disabled="page + 1 >= pages"
        @click="page++"
      >
        下一页
      </button>
    </footer>
  </section>
</template>

<style scoped>
.subtitle-preview { min-width: 0; min-height: min(48vh, 620px); display: flex; flex-direction: column; border: 1px solid var(--line-soft); border-radius: 14px; background: var(--surface); overflow: hidden; }
.subtitle-preview-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 16px 20px; border-bottom: 1px solid var(--line-soft); }
.subtitle-preview-heading p, .subtitle-preview-time, .subtitle-preview-empty p { color: var(--muted); font-size: var(--text-sm); }
.subtitle-preview-empty { display: grid; align-content: center; justify-items: center; gap: 8px; flex: 1; padding: 32px 20px; text-align: center; }
.subtitle-preview-list { max-height: 55vh; overflow: auto; }
.subtitle-preview-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px 24px; padding: 18px 20px; border-bottom: 1px solid var(--line-soft); }
.subtitle-preview-time { grid-column: 1 / -1; display: flex; justify-content: space-between; }
.subtitle-preview-row p { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.65; }
.subtitle-preview-translation { font-weight: 500; }
.subtitle-preview-warn .subtitle-preview-time { color: #9b5a10; }
.subtitle-preview-pagination { display: flex; align-items: center; justify-content: center; gap: 16px; padding: 12px; margin-top: auto; }
@container run (max-width: 700px) {
  .subtitle-preview-row { grid-template-columns: minmax(0, 1fr); }
  .subtitle-preview-heading { flex-wrap: wrap; }
}
</style>
