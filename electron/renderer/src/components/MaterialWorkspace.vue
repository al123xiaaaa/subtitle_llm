<script setup lang="ts">
import { computed, ref } from 'vue';
import { useMaterialWorkspace } from '../composables/useMaterialWorkspace';
import WorkspaceCues from './WorkspaceCues.vue';
import type { Issue, Source, Version } from '../composables/workspaceTypes';
const emit = defineEmits<{create: []; resume: [taskId: string]; source: [value: Source]; tool: [kind: 'download' | 'transcribe', value: Source]}>();
const ws = useMaterialWorkspace();
const {materials, materialId, language, material, languages, versions, version, view, message, loading, includeAll, selected, editCue, additional, running, items, undoConflict} = ws;
const forkModel = ref('');
const association = ref('');
async function associate() {
  if (!version.value || !association.value) return;
  const result = await window.subtitleLLM.workspaceRequest({action: 'associate', task_id: version.value.task_id, material_id: association.value});
  if (!result.ok) throw new Error(result.error);
  await ws.refresh(); await ws.openVersion(version.value.task_id);
}
function issueSummary(issue: Issue) {
  const labels: Record<string, string> = {semantic_meaning:'意思可能改变', semantic_omission:'可能遗漏信息', semantic_addition:'可能增加无依据信息', semantic_terminology:'术语或实体可能误译'};
  return issue.candidate_only ? labels[issue.type] || issue.description : issue.description;
}
const checkState = computed(() => {
  const state = version.value?.check_state;
  return state === 'checked' ? '检查已完成' : state === 'incomplete' ? '检查未完成' : '检查证据未知';
});
const pendingCount = computed(() => version.value?.review_items.filter(i => !i.outdated && i.state === 'pending').length || 0);
const usage = computed(() => {
  const doc = version.value;
  const operations = doc?.operations || [];
  const known = (items: typeof operations) => items.reduce((sum, item) => sum + (typeof item.usage === 'number' ? item.usage : 0), 0);
  return {manual: known(operations.filter(item => !item.automatic)), automatic: known(operations.filter(item => item.automatic)),
    total: (doc?.report.first_pass_tokens || 0) + (doc?.report.summary_tokens_total || 0) + known(operations),
    unknown: operations.some(item => typeof item.usage !== 'number') || Boolean(doc?.report.token_usage?.unknown_usage_calls)};
});
const statuses: Record<string, string> = {created:'待开始', processing_chunks:'翻译中', prepare_translation:'准备翻译', preparing_input:'准备素材', completed:'执行完成', completed_with_warnings:'执行完成，有待回顾项', failed:'执行失败', stopped:'已停止', pending:'待复核', accepted:'已保留当前译文', check_pending:'待补查', checked:'已检查', unavailable:'服务未完成', candidate:'候选未采用', uncertain:'证据不足', applied:'已应用', running:'处理中', derived:'已创建派生任务', not_checked:'尚未检查'};
function label(value: string) { return statuses[value] || value; }
function back() { materialId.value = ''; version.value = null; }
function chooseLanguage(event: Event) { if (material.value) void ws.guarded(() => ws.openMaterial(material.value!, (event.target as HTMLSelectElement).value)); }
function chooseVersion(event: Event) { void ws.guarded(() => ws.openVersion((event.target as HTMLSelectElement).value)); }
function checkDetails(id: string) { return version.value?.checks.find(c => c.check_id === id)?.details; }
function openFile(path: string) { void window.subtitleLLM.openPath(path); }
function checkScope(doc: Version) { return doc.checks.filter(c => !c.outdated).map(c => c.indices); }
function sourceForVersion(doc: Version): Source { return {path:doc.source, source_url:doc.source_url, source_video:doc.source_video, language:doc.source_language}; }
</script>
<template>
  <section
    class="material-workspace"
    aria-label="素材库"
  >
    <header class="workspace-heading">
      <div>
        <p class="workspace-eyebrow">
          SUBTITLE LIBRARY
        </p><h1>{{ material?.title || '素材库' }}</h1><p>译文、复核和交付结果，都留在对应素材中。</p>
      </div>
      <div class="workspace-actions">
        <button @click="ws.guarded(ws.refresh)">
          {{ loading ? '刷新中…' : '刷新' }}
        </button><button
          class="primary"
          @click="emit('create')"
        >
          新建翻译
        </button>
      </div>
    </header>
    <p
      v-if="message"
      role="status"
      class="workspace-notice"
    >
      {{ message }}
    </p>
    <template v-if="!materialId">
      <div
        v-if="!materials.length"
        class="workspace-empty"
      >
        <h2>从第一份素材开始</h2><p>选择字幕或视频地址，翻译完成后可随时回顾疑点和修改记录。</p><button @click="emit('create')">
          添加素材并翻译
        </button>
      </div>
      <div class="material-grid">
        <button
          v-for="entry in materials"
          :key="entry.material_id"
          class="material-card"
          @click="ws.guarded(() => ws.openMaterial(entry))"
        >
          <span class="material-monogram">字</span><h2>{{ entry.title }}</h2><p>{{ Object.keys(entry.defaults).join(' · ') || '尚无完整版本' }}</p><span>{{ entry.versions.length }} 个完整版本 · {{ entry.tasks.length - entry.versions.length }} 个未完成任务</span>
        </button>
      </div>
    </template>
    <template v-else-if="material && !version">
      <button @click="back">
        ← 素材库
      </button>
      <p>素材已保存，尚未生成翻译版本。可直接使用源字幕开始翻译，或先转写已有音视频。</p>
      <article
        v-for="source in material.sources || []"
        :key="source.source_id"
        class="history-card"
      >
        <p>{{ source.language }} · {{ source.path || source.source_video }}</p>
        <button
          v-if="source.path"
          @click="emit('source', source)"
        >
          使用此源字幕翻译
        </button>
        <button
          v-if="source.source_video"
          @click="emit('tool', 'transcribe', source)"
        >
          系统转写此素材
        </button>
        <button
          v-if="source.source_url"
          @click="emit('tool', 'download', source)"
        >
          重新下载此素材字幕
        </button>
      </article>
    </template>
    <template v-else-if="version">
      <div class="workspace-actions version-selector">
        <button @click="back">
          ← 素材库
        </button>
        <label>目标语言 <select
          :value="language"
          @change="chooseLanguage"
        ><option
          v-for="lang in languages"
          :key="lang"
        >{{ lang }}</option></select></label>
        <label>版本 / 任务 <select
          :value="version.task_id"
          @change="chooseVersion"
        ><option
          v-for="entry in versions"
          :key="entry.task_id"
          :value="entry.task_id"
        >{{ entry.complete ? '完整版本' : '未完成任务' }} · {{ new Date(entry.created_at).toLocaleString() }} · {{ entry.task_id.slice(0, 6) }}</option></select></label>
        <button
          v-if="version.complete"
          @click="ws.guarded(ws.pin)"
        >
          {{ material?.pinned[language] === version.task_id ? '取消固定默认' : '固定为默认版本' }}
        </button>
      </div>
      <div class="version-statuses">
        <span>{{ version.complete ? '译文完整，可使用' : '译文未完成' }}</span><span>{{ label(version.status) }}</span><span>{{ checkState }}</span><span>{{ pendingCount ? `${pendingCount} 组待复核` : '没有待复核疑点' }}</span><span>修改 r{{ version.revision }}</span>
      </div>
      <p
        v-if="version.legacy"
        class="workspace-note"
      >
        旧任务已保留。无法恢复的历史检查、模型或文件依据标为未知，不能视为已通过。
      </p>
      <nav
        class="workspace-tabs"
        aria-label="素材详情"
      >
        <button
          :class="{active: view === 'result'}"
          @click="view = 'result'"
        >
          译文与交付
        </button><button
          :class="{active: view === 'review'}"
          @click="view = 'review'"
        >
          问题回顾 {{ pendingCount || '' }}
        </button><button
          :class="{active: view === 'history'}"
          @click="view = 'history'"
        >
          修改与用量
        </button>
      </nav>
      <template v-if="view === 'result'">
        <div class="workspace-actions">
          <template v-if="version.complete">
            <button
              class="primary"
              @click="ws.guarded(() => ws.exportArtifact('subtitle'))"
            >
              导出当前字幕
            </button><button @click="ws.guarded(() => ws.exportArtifact('video'))">
              生成 / 重试视频
            </button>
          </template>
          <template v-else>
            <button
              class="primary"
              @click="emit('resume', version.task_id)"
            >
              继续此任务
            </button><button @click="ws.guarded(() => ws.exportArtifact('subtitle', true))">
              明确导出已完成部分
            </button>
          </template>
          <button @click="ws.guarded(() => ws.fork(undefined, true))">
            整部重跑为新版本
          </button>
        </div>
        <p
          v-if="running.has(version.task_id)"
          role="status"
        >
          正在生成或检查，可继续编辑字幕；完成的文件会标明是否需要更新。
        </p>
        <div
          v-if="!version.complete"
          class="workspace-note"
        >
          部分导出保留原时间轴，文件名包含“未完成”；不会成为默认完整版本。
        </div>
        <div
          v-for="artifact in version.artifacts"
          :key="artifact.artifact_id"
          class="artifact-row"
        >
          <strong>{{ artifact.kind === 'video' ? '视频' : '字幕' }} · r{{ artifact.revision ?? '未知' }}</strong><span>{{ artifact.status === 'ready' ? '已生成' : artifact.status === 'failed' ? '生成失败' : '生成中 / 待恢复' }}{{ artifact.outdated ? ' · 需要更新' : '' }}{{ artifact.missing ? ' · 文件已移走' : '' }}{{ artifact.partial ? ' · 未完成部分' : '' }}</span><button
            v-if="artifact.path && !artifact.missing"
            @click="openFile(artifact.path)"
          >
            打开文件
          </button><p v-if="artifact.error">
            {{ artifact.error }}
          </p><p v-if="artifact.missing_indices.length">
            未包含行：{{ artifact.missing_indices.join('、') }}
          </p>
        </div>
        <details class="workspace-details">
          <button
            v-if="version.source_url"
            @click="emit('tool', 'download', sourceForVersion(version))"
          >
            下载此素材字幕
          </button>
          <button
            v-if="version.source_video"
            @click="emit('tool', 'transcribe', sourceForVersion(version))"
          >
            系统转写此素材
          </button>
          <summary>源字幕与模型来源 / 换模型接续</summary><p>{{ version.source_url || version.source }}</p><p>当前源字幕：{{ version.source }}</p><p v-if="version.parent_task_id">
            派生自 {{ version.parent_task_id }}
          </p><p
            v-for="(origin, index) in version.provenance"
            :key="index"
          >
            {{ origin.model || '模型未知' }} · {{ origin.indices.length }} 条{{ origin.inherited ? '（继承）' : '' }}
          </p><label>同一服务的新翻译模型 <input
            v-model="forkModel"
            placeholder="输入模型 ID"
          ></label><button
            :disabled="!forkModel.trim()"
            @click="ws.guarded(() => ws.fork(forkModel))"
          >
            创建新任务接续已完成部分
          </button><p>当前版本、原文和旧模型来源均保留。换服务可在新建翻译设置中选择。</p>
          <label>明确关联到已有素材 <select v-model="association"><option value="">请选择</option><option
            v-for="entry in materials.filter(entry => entry.material_id !== materialId)"
            :key="entry.material_id"
            :value="entry.material_id"
          >{{ entry.title }}</option></select></label><button
            :disabled="!association"
            @click="ws.guarded(associate)"
          >
            关联此版本
          </button>
        </details>
        <WorkspaceCues
          :entries="version.entries"
          :protected-indices="version.protected_indices"
          editable
          @edit="ws.startEdit"
        />
      </template>
      <template v-if="view === 'review'">
        <div class="workspace-actions">
          <label><input
            v-model="includeAll"
            type="checkbox"
          > 查看全部历史</label><button
            :disabled="!selected.length"
            @click="ws.guarded(ws.accept)"
          >
            保留所选当前译文（{{ selected.length }}）
          </button>
        </div>
        <p class="workspace-note">
          疑点是检查候选。保留译文会记录人工决定并保护该范围，不代表检查通过。无需听音。
        </p>
        <p v-if="!items.length">
          当前没有待处理记录。{{ checkState === '检查证据未知' ? '此版本缺少检查证据，可主动补查。' : '' }}
        </p>
        <button
          v-if="!checkScope(version).length"
          @click="ws.guarded(() => ws.prepare('check'))"
        >
          为当前版本补查
        </button>
        <article
          v-for="item in items"
          :key="item.item_id"
          class="review-card"
          :class="{outdated: item.outdated}"
        >
          <header>
            <label v-if="item.can_accept && item.state === 'pending'"><input
              v-model="selected"
              type="checkbox"
              :value="item.item_id"
            > 选择此项</label><strong>{{ item.indices.length > 8 ? `${item.indices.length} 条相关字幕` : `第 ${item.indices.join('、')} 行` }}</strong><span>{{ item.outdated ? '依据已失效' : label(item.state) }}</span>
          </header>
          <p v-if="item.state === 'check_pending'">
            {{ item.check_status === 'unavailable' ? '检查服务未完成，译文可继续使用。' : '尚未完成基于当前内容的语义检查。' }}
          </p>
          <p
            v-for="(issue, index) in item.issues"
            :key="index"
          >
            {{ issueSummary(issue) }} <span v-if="issue.candidate_only">（待核验候选）</span>
          </p>
          <WorkspaceCues
            :entries="item.context.filter(c => item.indices.includes(c.index))"
            :protected-indices="version.protected_indices"
            :editable="!item.outdated"
            @edit="ws.startEdit"
          />
          <details><summary>完整片段上下文、检查依据与概率</summary><WorkspaceCues :entries="item.context" /><pre>{{ JSON.stringify(checkDetails(item.check_id), null, 2) }}</pre><p>概率用于排序，不保证错误率；定位份额不是逐行错误概率。</p></details>
          <div
            v-if="!item.outdated"
            class="workspace-actions"
          >
            <button @click="ws.guarded(() => ws.prepare('check', item))">
              按完整片段补查
            </button><button
              v-if="item.can_accept && item.state === 'pending'"
              @click="ws.guarded(() => ws.prepare('repair', item))"
            >
              核验并尝试局部修复
            </button><button @click="ws.guarded(() => ws.prepare('retranscribe', item))">
              系统重新识别此范围
            </button>
          </div>
        </article>
      </template>
      <template v-if="view === 'history'">
        <div class="budget-panel">
          <h2>资源用量</h2><p>首轮累计 {{ version.legacy ? '未知' : version.report.first_pass_tokens ?? '未知' }} tokens</p><p v-if="version.budget">
            自动额外额度 {{ version.budget.limit }} · 已知使用 {{ version.budget.spent }} · 预留 / 未知 {{ version.budget.reserved }} · 可用 {{ version.budget.available }}
          </p><p>额外额度默认是首轮 token 的 30%，不等于金额的 30%。费用未知；本版不承诺金额停止线。</p>
          <p>摘要已知 {{ version.report.summary_tokens_total ?? '未知' }} · 自动追加已知 {{ usage.automatic }} · 人工追加已知 {{ usage.manual }} tokens</p>
          <p>累计已知部分 {{ usage.total }} tokens{{ usage.unknown || version.legacy ? '；仍有未知用量，不能作为完整总数' : '' }}。估算用量不计入可靠额度基准。</p>
          <p v-if="version.origin_operation">
            本次新源翻译与验收计入原版本的同一笔追加操作：<button @click="ws.guarded(() => ws.openVersion(version!.origin_operation!.task_id))">
              查看来源及用量
            </button>
          </p>
        </div>
        <article
          v-for="operation in [...(version.operations || [])].reverse()"
          :key="operation.operation_id"
          class="history-card"
        >
          <strong>{{ operation.automatic ? '自动处理' : '手动追加' }} · {{ label(operation.status) }}</strong><p>范围 {{ operation.indices.join('、') }} · {{ operation.usage === null ? '用量未返回，保留额度' : `${operation.usage} tokens` }}</p><p>{{ operation.error }}</p><button
            v-if="operation.derived_task_id"
            @click="ws.guarded(() => ws.openVersion(operation.derived_task_id!))"
          >
            查看派生任务
          </button><p v-if="operation.audio_seconds">
            本地识别范围 {{ operation.audio_seconds }} 秒 · 处理耗时 {{ operation.elapsed_seconds ?? '处理中' }} 秒；本地计算不计作服务商 tokens。
          </p><details v-if="operation.candidate">
            <summary>保留的候选稿</summary><pre>{{ JSON.stringify(operation.candidate, null, 2) }}</pre>
          </details>
        </article>
        <article
          v-for="change in [...version.history].reverse()"
          :key="change.id"
          class="history-card"
        >
          <strong>{{ change.kind === 'accept' ? '人工保留并保护' : change.kind === 'undo' ? '局部撤销' : change.kind === 'repair' ? '验收后自动修复' : change.kind === 'edit' ? '人工编辑' : '版本来源变更' }}{{ change.category === 'timing' ? ' · 时间' : change.category === 'translation' ? ' · 译文' : '' }}</strong><span>{{ new Date(change.at).toLocaleString() }}</span><p>{{ change.reason }}</p><details v-if="change.before">
            <summary>查看前后差异</summary><h3>修改前</h3><WorkspaceCues :entries="change.before" /><h3>修改后</h3><WorkspaceCues :entries="change.after || []" />
          </details><button
            v-if="change.before"
            @click="ws.guarded(() => ws.undo(change.id))"
          >
            撤销此范围
          </button>
        </article>
      </template>
    </template>
    <div
      v-if="editCue"
      class="workspace-modal"
      role="dialog"
      aria-modal="true"
      aria-label="编辑字幕"
    >
      <form @submit.prevent="ws.guarded(ws.saveEdit)">
        <h2>编辑第 {{ editCue.index }} 行</h2><p>{{ editCue.original_text }}</p><label>译文<textarea
          v-model="editCue.translated_text"
          required
          rows="5"
        /></label><div class="workspace-actions">
          <label>开始<input
            v-model="editCue.start_time"
            required
          ></label><label>结束<input
            v-model="editCue.end_time"
            required
          ></label>
        </div><p>保存后立即生效并受人工保护，相关旧检查会失效。</p><p
          v-if="message"
          role="alert"
        >
          {{ message }}
        </p><div class="workspace-actions">
          <button
            type="button"
            @click="editCue = null"
          >
            取消
          </button><button class="primary">
            保存修改
          </button>
        </div>
      </form>
    </div>
    <div
      v-if="additional"
      class="workspace-modal"
      role="dialog"
      aria-modal="true"
      aria-label="追加处理额度"
    >
      <form @submit.prevent="ws.guarded(ws.runAdditional)">
        <h2>{{ additional.action === 'repair' ? '局部修复' : additional.action === 'check' ? '补充检查' : '系统重新识别' }}</h2><p>本次单独授权 {{ additional.indices.length }} 条相关字幕，不扩大其他范围的自动额度。</p><p>预计预留 {{ additional.estimated_tokens }} tokens（含核验及验收）；费用未知。</p><label>本次 token 上限<input
          v-model.number="additional.token_limit"
          type="number"
          :min="additional.estimated_tokens"
          step="1"
          required
        ></label><p v-if="additional.action === 'retranscribe'">
          系统读取对应音视频，单次最多 120 秒。没有证据或判断不确定时保留候选，不要求听音。
        </p><div class="workspace-actions">
          <button
            type="button"
            @click="additional = null"
          >
            取消
          </button><button class="primary">
            开始本次处理
          </button>
        </div>
      </form>
    </div>
    <div
      v-if="undoConflict"
      class="workspace-modal"
      role="dialog"
      aria-modal="true"
      aria-label="撤销冲突"
    >
      <div>
        <h2>该范围有后续修改</h2><p>强制恢复会覆盖该范围的后续编辑，其他范围保持当前内容。</p><div class="workspace-actions">
          <button @click="undoConflict = null">
            保留当前内容
          </button><button @click="ws.guarded(() => ws.undo(undoConflict!, true))">
            明确恢复该范围
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style src="../styles/material-workspace.css"></style>
