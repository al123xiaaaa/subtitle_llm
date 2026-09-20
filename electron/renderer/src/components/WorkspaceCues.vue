<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { Cue } from '../composables/workspaceTypes';
const props = defineProps<{entries: Cue[]; protectedIndices?: number[]; editable?: boolean}>();
defineEmits<{edit: [cue: Cue]}>();
const page = ref(0);
const visible = computed(() => props.entries.slice(page.value * 30, (page.value + 1) * 30));
watch(() => props.entries, () => { page.value = 0; });
</script>
<template>
  <div class="cue-list">
    <article
      v-for="cue in visible"
      :key="cue.index"
      class="cue-row"
    >
      <div class="cue-time">
        #{{ cue.index }} · {{ cue.start_time }} → {{ cue.end_time }}
        <span v-if="protectedIndices?.includes(cue.index)"> · 人工保护</span>
        <button
          v-if="editable"
          type="button"
          @click="$emit('edit', cue)"
        >
          编辑
        </button>
      </div>
      <div class="cue-pair">
        <p>{{ cue.original_text }}</p><p>{{ cue.translated_text || '尚无译文' }}</p>
      </div>
    </article>
    <div
      v-if="entries.length > 30"
      class="workspace-actions"
    >
      <button
        :disabled="page === 0"
        @click="page--"
      >
        上一页
      </button>
      <span>{{ page + 1 }} / {{ Math.ceil(entries.length / 30) }} 页 · 共 {{ entries.length }} 条</span>
      <button
        :disabled="(page + 1) * 30 >= entries.length"
        @click="page++"
      >
        下一页
      </button>
    </div>
  </div>
</template>
