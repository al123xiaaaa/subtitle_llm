import { computed, onMounted, onUnmounted, ref } from 'vue';
import type { AdditionalRequest, Cue, Material, ReviewItem, Version } from './workspaceTypes';

export function useMaterialWorkspace() {
  const materials = ref<Material[]>([]);
  const materialId = ref('');
  const language = ref('');
  const version = ref<Version | null>(null);
  const view = ref<'result' | 'review' | 'history'>('result');
  const message = ref('');
  const loading = ref(false);
  const includeAll = ref(false);
  const selected = ref<string[]>([]);
  const editCue = ref<Cue | null>(null);
  const editRevision = ref(0);
  const editTaskId = ref('');
  const additional = ref<AdditionalRequest | null>(null);
  const running = ref(new Set<string>());
  const undoConflict = ref<string | null>(null);
  const material = computed(() => materials.value.find(m => m.material_id === materialId.value));
  const languages = computed(() => [...new Set(material.value?.tasks.map(v => v.language) || [])]);
  const versions = computed(() => material.value?.tasks.filter(v => v.language === language.value) || []);
  const items = computed(() => (version.value?.review_items || []).filter(item => includeAll.value || (!item.outdated && ['pending', 'check_pending'].includes(item.state))));
  let selectionGeneration = 0;
  class RequestError extends Error { constructor(text: string, readonly code?: string) { super(text); } }
  async function request<T>(payload: Record<string, unknown>): Promise<T> {
    const response = await window.subtitleLLM.workspaceRequest(JSON.parse(JSON.stringify(payload)) as Record<string, unknown>);
    if (!response.ok) throw new RequestError(response.error || '操作未完成', response.code);
    return response.value as T;
  }
  async function guarded(work: () => Promise<void>) {
    message.value = '';
    try { await work(); }
    catch (error) { message.value = error instanceof Error ? error.message : '操作未完成'; }
  }
  async function refresh() {
    loading.value = true;
    try { materials.value = await request<Material[]>({action: 'library'}); }
    finally { loading.value = false; }
  }
  async function openVersion(taskId: string) {
    const generation = ++selectionGeneration;
    const result = await request<Version>({action: 'version', task_id: taskId});
    if (generation !== selectionGeneration) return;
    version.value = result; materialId.value = result.material_id; language.value = result.language; selected.value = [];
  }
  async function openMaterial(value: Material, target?: string) {
    selectionGeneration += 1;
    version.value = null;
    materialId.value = value.material_id;
    language.value = target || Object.keys(value.defaults)[0] || value.tasks[0]?.language || '';
    const id = value.defaults[language.value] || value.tasks.find(v => v.language === language.value)?.task_id;
    if (id) await openVersion(id);
  }
  async function reload(taskId: string) {
    await refresh();
    if (version.value?.task_id === taskId) await openVersion(taskId);
  }
  async function accept() {
    if (!version.value) return;
    const taskId = version.value.task_id;
    await request({action: 'accept', task_id: taskId, item_ids: selected.value});
    await reload(taskId);
  }
  function startEdit(cue: Cue) { editCue.value = {...cue}; editRevision.value = version.value?.revision || 0; editTaskId.value = version.value?.task_id || ''; }
  async function saveEdit() {
    if (!editCue.value || !editTaskId.value) return;
    const taskId = editTaskId.value;
    await request({action: 'edit', task_id: taskId, revision: editRevision.value, changes: {[editCue.value.index]: {
      translated_text: editCue.value.translated_text, start_time: editCue.value.start_time, end_time: editCue.value.end_time,
    }}});
    editCue.value = null; await reload(taskId);
  }
  async function undo(historyId: string, force = false) {
    if (!version.value) return;
    const taskId = version.value.task_id;
    try { await request({action: 'undo', task_id: taskId, history_id: historyId, revision: version.value.revision, force}); }
    catch (error) {
      if (error instanceof RequestError && error.code === 'conflict') { undoConflict.value = historyId; await openVersion(taskId); return; }
      throw error;
    }
    undoConflict.value = null; await reload(taskId);
  }
  async function pin() {
    if (!version.value) return;
    await request({action: 'pin', material_id: materialId.value, language: language.value,
      task_id: material.value?.pinned[language.value] === version.value.task_id ? null : version.value.task_id});
    await refresh();
  }
  async function prepare(action: AdditionalRequest['action'], item?: ReviewItem) {
    if (!version.value) return;
    const doc = version.value;
    const generation = selectionGeneration;
    const indices = item ? (action === 'retranscribe' ? item.indices : item.context.map(c => c.index)) : version.value.entries.map(c => c.index);
    const result = await request<{estimated_tokens: number}>({action: 'estimate', task_id: doc.task_id, indices, repair: action !== 'check'});
    if (generation !== selectionGeneration) return;
    additional.value = {action, task_id: doc.task_id, revision: doc.revision, indices, item_id: item?.item_id, estimated_tokens: result.estimated_tokens, token_limit: result.estimated_tokens};
  }
  async function runAdditional() {
    if (!additional.value) return;
    const taskId = additional.value.task_id;
    const payload = {...additional.value};
    additional.value = null; running.value.add(taskId);
    try { await request(payload); await reload(taskId); message.value = '追加处理已结束，结果与用量已记录。'; }
    finally { running.value.delete(taskId); }
  }
  async function exportArtifact(kind: 'subtitle' | 'video', partial = false) {
    if (!version.value) return;
    const doc = version.value;
    const directory = await window.subtitleLLM.selectDirectory();
    if (!directory) return;
    const extension = kind === 'subtitle' ? 'srt' : 'mkv';
    const filename = `${material.value?.title || '字幕'}.${doc.language}.r${doc.revision}.${Date.now()}.${extension}`;
    const path = `${directory}/${filename}`;
    let video = doc.source_video;
    if (kind === 'video' && !video) video = await window.subtitleLLM.selectVideo() || undefined;
    if (kind === 'video' && !video) return;
    running.value.add(doc.task_id);
    try { await request({action: `export_${kind}`, task_id: doc.task_id, path, partial, video}); await reload(doc.task_id); }
    finally { running.value.delete(doc.task_id); }
  }
  async function fork(model?: string, rerun = false) {
    if (!version.value) return;
    const doc = await request<Version>({action: 'fork', task_id: version.value.task_id, model, rerun});
    await refresh(); await openVersion(doc.task_id);
    message.value = '已创建独立任务并保存继承来源，点击“继续此任务”开始。';
  }
  let unsubscribe: (() => void) | undefined;
  onMounted(() => {
    void guarded(refresh);
    unsubscribe = window.subtitleLLM.onJobEvent(event => { if (event.type === 'finished') void guarded(refresh); });
  });
  onUnmounted(() => unsubscribe?.());
  return {materials, materialId, language, version, material, languages, versions, view, message, loading, includeAll, selected,
    editCue, additional, running, items, undoConflict, guarded, refresh, openMaterial, openVersion, accept, startEdit, saveEdit,
    undo, pin, prepare, runAdditional, exportArtifact, fork};
}
