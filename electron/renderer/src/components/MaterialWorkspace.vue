<script setup lang="ts">
import { computed, ref } from 'vue';
import { ArrowLeft, ArrowUpRight, Check, CheckCheck, ChevronRight, Clock3, FileText, FolderOpen, History, Info, Languages, LibraryBig, Pin, Plus, RefreshCw, ShieldCheck, Sparkles } from '@lucide/vue';
import { useMaterialWorkspace } from '../composables/useMaterialWorkspace';
import WorkspaceCues from './WorkspaceCues.vue';
import type { Issue, Source, Version } from '../composables/workspaceTypes';
const emit = defineEmits<{create: []; resume: [taskId: string]; source: [value: Source]; tool: [kind: 'download' | 'transcribe', value: Source]}>();
const ws = useMaterialWorkspace();
const {materials, materialId, language, material, languages, versions, version, view, message, loading, includeAll, selected, editCue, additional, running, items, undoConflict} = ws;
const forkModel = ref('');
const association = ref('');
const languageNames: Record<string, string> = {Chinese:'简体中文', chinese:'简体中文', zh:'简体中文', English:'英语', en:'英语', Japanese:'日语', ja:'日语', Korean:'韩语', ko:'韩语'};
function languageName(value: string) { return languageNames[value] || value; }
function dateLabel(value: string) { return new Date(value).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}); }
const missingCheckCount = computed(() => items.value.filter(item => item.state === 'check_pending').length);

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
  const known = (calls: typeof operations) => calls.reduce((sum, item) => sum + (typeof item.usage === 'number' ? item.usage : 0), 0);
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
function sourceForVersion(doc: Version): Source { return {material_id:doc.material_id, path:doc.source, source_url:doc.source_url, source_video:doc.source_video, language:doc.source_language}; }
</script>
<template>
  <section
    class="material-workspace"
    aria-label="素材库"
  >
    <div class="workspace-content">
      <div class="workspace-breadcrumb">
        <button
          v-if="materialId"
          class="text-button"
          @click="back"
        >
          <ArrowLeft :size="14" />素材库
        </button>
        <span v-else><LibraryBig :size="14" />工作空间</span>
        <ChevronRight :size="12" /><span>{{ materialId ? '素材详情' : '素材库' }}</span>
      </div>
      <header class="workspace-heading">
        <div class="workspace-title">
          <h1>{{ material?.title || '素材库' }}</h1>
          <p v-if="!materialId">
            每一份素材，从第一遍翻译到最后一次打磨。
          </p>
        </div>
        <div class="workspace-actions heading-actions">
          <button
            class="refresh-button"
            aria-label="刷新"
            title="刷新素材"
            :disabled="loading"
            @click="ws.guarded(ws.refresh)"
          >
            <RefreshCw
              :size="15"
              :class="{'is-spinning': loading}"
            />
          </button>
          <button
            class="primary"
            @click="emit('create')"
          >
            <Plus :size="15" />新建翻译
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
          <span class="empty-symbol"><LibraryBig :size="26" /></span><h2>从第一份素材开始</h2><p>选择字幕或视频地址，翻译完成后可随时回顾疑点和修改记录。</p><button @click="emit('create')">
            添加素材并翻译
          </button>
        </div>
        <div
          v-if="materials.length"
          class="section-heading library-heading"
        >
          <h2>全部素材 <span class="count-label">{{ materials.length }}</span></h2><span>按素材整理，随时继续</span>
        </div>
        <div class="material-grid">
          <button
            v-for="entry in materials"
            :key="entry.material_id"
            class="material-card"
            @click="ws.guarded(() => ws.openMaterial(entry))"
          >
            <span class="material-card-top"><span class="material-monogram"><FileText :size="22" /></span><ArrowUpRight :size="16" /></span>
            <h2>{{ entry.title }}</h2><p>{{ Object.keys(entry.defaults).map(languageName).join(' · ') || '尚无完整版本' }}</p>
            <span class="material-card-foot"><span>{{ entry.versions.length }} 个完整版本</span><span>{{ entry.tasks.length - entry.versions.length }} 个未完成任务</span></span>
          </button>
        </div>
      </template>
      <template v-else-if="material && !version">
        <p>素材已保存，尚未生成翻译版本。可直接使用源字幕开始翻译，或先转写已有音视频。</p>
        <article
          v-for="source in material.sources || []"
          :key="source.source_id"
          class="history-card"
        >
          <p>{{ source.language }} · {{ source.path || source.source_video }}</p>
          <button
            v-if="source.path"
            @click="emit('source', {...source, material_id:materialId})"
          >
            使用此源字幕翻译
          </button>
          <button
            v-if="source.source_video"
            @click="emit('tool', 'transcribe', {...source, material_id:materialId})"
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
        <section
          class="version-overview"
          aria-label="当前版本"
        >
          <div class="version-selector">
            <label class="language-selector"><span><Languages :size="13" />目标语言</span><select
              :value="language"
              @change="chooseLanguage"
            ><option
              v-for="lang in languages"
              :key="lang"
              :value="lang"
            >{{ languageName(lang) }}</option></select></label>
            <label class="version-picker"><span><History :size="13" />版本 / 任务</span><select
              :value="version.task_id"
              @change="chooseVersion"
            ><option
              v-for="entry in versions"
              :key="entry.task_id"
              :value="entry.task_id"
            >{{ entry.complete ? '完整版本' : '未完成任务' }} · {{ dateLabel(entry.created_at) }} · {{ entry.task_id.slice(0, 6) }}</option></select></label>
            <button
              v-if="version.complete"
              class="pin-button"
              :class="{'is-pinned': material?.pinned[language] === version.task_id}"
              @click="ws.guarded(ws.pin)"
            >
              <Pin :size="14" />{{ material?.pinned[language] === version.task_id ? '取消固定默认' : '固定为默认版本' }}
            </button>
          </div>
          <div class="version-statuses">
            <span
              class="workspace-badge"
              :class="version.complete ? 'is-success' : 'is-warning'"
            ><Check
              v-if="version.complete"
              :size="13"
            /><Clock3
              v-else
              :size="13"
            />{{ version.complete ? '译文完整，可使用' : '译文未完成' }}</span>
            <span
              class="workspace-badge"
              :class="version.check_state === 'checked' ? 'is-success' : 'is-neutral'"
            ><ShieldCheck :size="13" />{{ checkState }}</span>
            <span
              v-if="pendingCount"
              class="workspace-badge is-warning"
            >{{ pendingCount }} 组待复核</span>
            <span
              v-else
              class="status-caption"
            >没有待复核疑点</span>
            <span class="version-meta">{{ label(version.status) }}<span>·</span>修改 r{{ version.revision }}</span>
          </div>
        </section>
        <p
          v-if="version.legacy"
          class="workspace-note legacy-note"
        >
          <Info :size="15" /><span>旧任务已保留。历史检查、模型或文件依据无法恢复的部分标为未知，不代表已通过。</span>
        </p>
        <nav
          class="workspace-tabs"
          aria-label="素材详情"
        >
          <button
            :class="{active: view === 'result'}"
            :aria-pressed="view === 'result'"
            @click="view = 'result'"
          >
            <FileText :size="16" />译文与交付
          </button><button
            :class="{active: view === 'review'}"
            :aria-pressed="view === 'review'"
            @click="view = 'review'"
          >
            <ShieldCheck :size="16" />问题回顾 <span
              v-if="pendingCount"
              class="tab-count"
            >{{ pendingCount }}</span>
          </button><button
            :class="{active: view === 'history'}"
            :aria-pressed="view === 'history'"
            @click="view = 'history'"
          >
            <History :size="16" />修改与用量
          </button>
        </nav>
        <template v-if="view === 'result'">
          <div class="section-heading result-toolbar">
            <div><h2>双语字幕 <span class="count-label">{{ version.entries.length }} 条</span></h2><p>对照原文检查译文，修改会自动保存到当前版本。</p></div>
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
            <span class="artifact-icon"><FileText :size="18" /></span><strong>{{ artifact.kind === 'video' ? '视频' : '字幕' }} · r{{ artifact.revision ?? '未知' }}</strong><span
              class="artifact-state"
              :class="{'needs-update': artifact.outdated || artifact.missing || artifact.status === 'failed'}"
            >{{ artifact.status === 'ready' ? '已生成' : artifact.status === 'failed' ? '生成失败' : '生成中 / 待恢复' }}{{ artifact.outdated ? ' · 需要更新' : '' }}{{ artifact.missing ? ' · 文件已移走' : '' }}{{ artifact.partial ? ' · 未完成部分' : '' }}</span><button
              v-if="artifact.path && !artifact.missing"
              @click="openFile(artifact.path)"
            >
              <FolderOpen :size="14" />打开文件
            </button><p v-if="artifact.error">
              {{ artifact.error }}
            </p><p v-if="artifact.missing_indices.length">
              未包含行：{{ artifact.missing_indices.join('、') }}
            </p>
          </div>
          <WorkspaceCues
            :entries="version.entries"
            :protected-indices="version.protected_indices"
            editable
            @edit="ws.startEdit"
          />
          <details class="workspace-details">
            <summary>源字幕与模型来源 / 换模型接续</summary>
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
            <p>{{ version.source_url || version.source }}</p><p>当前源字幕：{{ version.source }}</p><p v-if="version.parent_task_id">
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
        </template>
        <template v-if="view === 'review'">
          <div class="section-heading review-toolbar">
            <div><h2>{{ includeAll ? '全部复核记录' : '待处理记录' }} <span class="count-label">{{ items.length }} 组</span></h2><p>疑点 {{ pendingCount }} 组<span v-if="missingCheckCount"> · 待补查 {{ missingCheckCount }} 组</span></p></div>
            <div class="workspace-actions">
              <label class="workspace-check"><input
                v-model="includeAll"
                type="checkbox"
              >查看全部历史</label>
              <button
                :disabled="!selected.length"
                @click="ws.guarded(ws.accept)"
              >
                <Check :size="14" />保留所选当前译文（{{ selected.length }}）
              </button>
              <button
                v-if="!checkScope(version).length"
                class="primary"
                @click="ws.guarded(() => ws.prepare('check'))"
              >
                <Sparkles :size="14" />为当前版本补查
              </button>
            </div>
          </div>
          <p class="workspace-note review-explanation">
            <Info :size="15" /><span>疑点是检查候选。保留译文会记录人工决定并保护该范围，不代表检查通过。无需听音。</span>
          </p>
          <div
            v-if="!items.length"
            class="workspace-empty review-empty"
          >
            <span class="empty-symbol"><CheckCheck :size="26" /></span><h2>当前没有待处理记录。</h2><p>{{ checkState === '检查证据未知' ? '此版本缺少检查证据，可主动补查。' : '新的检查结果和修改记录会保留在这里，方便随时回顾。' }}</p>
          </div>
          <article
            v-for="item in items"
            :key="item.item_id"
            class="review-card"
            :class="{outdated: item.outdated}"
          >
            <header class="review-card-heading">
              <span
                class="review-type-icon"
                :class="{'is-pending': item.state === 'pending'}"
              ><ShieldCheck :size="18" /></span>
              <strong>{{ item.indices.length > 8 ? `${item.indices.length} 条相关字幕` : `第 ${item.indices.join('、')} 行` }}</strong>
              <span
                class="workspace-badge"
                :class="item.state === 'pending' && !item.outdated ? 'is-warning' : 'is-neutral'"
              >{{ item.outdated ? '依据已失效' : label(item.state) }}</span>
              <label
                v-if="item.can_accept && item.state === 'pending'"
                class="workspace-check review-select"
              ><input
                v-model="selected"
                type="checkbox"
                :value="item.item_id"
              >选择此项</label>
            </header>
            <p
              v-if="item.state === 'check_pending'"
              class="review-description"
            >
              {{ item.check_status === 'unavailable' ? '检查服务未完成，译文可继续使用。' : '尚未完成基于当前内容的语义检查。' }}
            </p>
            <p
              v-for="(issue, index) in item.issues"
              :key="index"
              class="review-description"
            >
              {{ issueSummary(issue) }} <span v-if="issue.candidate_only">（待核验候选）</span>
            </p>
            <details
              class="review-cues"
              :open="item.state !== 'check_pending'"
            >
              <summary>查看相关字幕 <span>{{ item.indices.length }} 条</span></summary>
              <WorkspaceCues
                :entries="item.context.filter(c => item.indices.includes(c.index))"
                :protected-indices="version.protected_indices"
                :editable="!item.outdated"
                @edit="ws.startEdit"
              />
            </details>
            <details class="review-evidence">
              <summary>完整片段上下文、检查依据与概率</summary><WorkspaceCues :entries="item.context" /><pre>{{ JSON.stringify(checkDetails(item.check_id), null, 2) }}</pre><p>概率用于排序，不保证错误率；定位份额不是逐行错误概率。</p>
            </details>
            <div
              v-if="!item.outdated"
              class="workspace-actions review-card-actions"
            >
              <button
                class="review-check-button"
                @click="ws.guarded(() => ws.prepare('check', item))"
              >
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
    </div>
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
