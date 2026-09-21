<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { LockKeyhole, Pencil, ChevronLeft, ChevronRight } from '@lucide/vue';
import type { Cue } from '../composables/workspaceTypes';
const props = defineProps<{entries: Cue[]; protectedIndices?: number[]; editable?: boolean}>();
defineEmits<{edit: [cue: Cue]}>();
const page = ref(0);
const visible = computed(() => props.entries.slice(page.value * 30, (page.value + 1) * 30));
watch(() => props.entries, () => { page.value = 0; });
</script>
<template>
  <div class="cue-list">
    <div
      class="cue-columns"
      aria-hidden="true"
    >
      <span>行号 / 时间</span><span>原文</span><span>译文</span><span />
    </div>
    <article
      v-for="cue in visible"
      :key="cue.index"
      class="cue-row"
    >
      <div class="cue-time">
        <span class="cue-index">{{ String(cue.index).padStart(2, '0') }}</span>
        <time>{{ cue.start_time }}</time><time>{{ cue.end_time }}</time>
        <span
          v-if="protectedIndices?.includes(cue.index)"
          class="cue-protected"
        ><LockKeyhole :size="11" />人工保护</span>
      </div>
      <div class="cue-pair">
        <p>{{ cue.original_text }}</p><p :class="{'cue-untranslated': !cue.translated_text}">
          {{ cue.translated_text || '尚无译文' }}
        </p>
      </div>
      <button
        v-if="editable"
        class="cue-edit"
        type="button"
        aria-label="编辑"
        :title="`编辑第 ${cue.index} 行`"
        @click="$emit('edit', cue)"
      >
        <Pencil :size="14" />
      </button>
    </article>
    <div
      v-if="entries.length > 30"
      class="workspace-actions cue-pagination"
    >
      <button
        :disabled="page === 0"
        @click="page--"
      >
        <ChevronLeft :size="14" />上一页
      </button>
      <span>{{ page + 1 }} / {{ Math.ceil(entries.length / 30) }} 页 · 共 {{ entries.length }} 条</span>
      <button
        :disabled="(page + 1) * 30 >= entries.length"
        @click="page++"
      >
        下一页<ChevronRight :size="14" />
      </button>
    </div>
  </div>
</template>
