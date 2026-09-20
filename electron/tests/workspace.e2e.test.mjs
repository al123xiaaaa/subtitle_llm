import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { _electron as electron } from 'playwright';
const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'subtitle-workspace-e2e-'));
const database = path.join(temp, 'translation-tasks.sqlite3');
const python = path.join(root, '.venv/bin/python');
const seed = `
import sys
sys.path.insert(0, 'src')
from pathlib import Path
from subtitle_llm.pipeline.task_store import TranslationTaskStore
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.settings import AppConfig, ModelConfig
from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.workspace import WorkspaceStore
store = TranslationTaskStore(sys.argv[1])
model = ModelConfig(type='custom', model='fake', api_key_env='FAKE_KEY', endpoint='https://fake.invalid')
record = store.create_task(task_id='e2e-version', input_display='课堂视频', working_directory=sys.argv[2], source_subtitle_path=str(Path(sys.argv[2])/'source.srt'), normalized_input_fingerprint='e2e-source', target_language='Chinese', source_language='en', output_format='source-first', output_file=str(Path(sys.argv[2])/'old.srt'), config=AppConfig(summary_model=model,translation_model=model))
entries=[SubtitleEntry(1,'00:00:01,000','00:00:03,000','We can try.','我们可以试试。'),SubtitleEntry(2,'00:00:03,000','00:00:05,000','It is a new day.','新的一天。')]
report=TranslationReport(input_file='source.srt',output_file=record.output_file,context_file='',workspace_recorded=True,translation_complete=True,total_entries=2,accepted_entry_indices=[1,2])
store.save_resume_state(record.task_id,Subtitle(entries),report)
store.update_status(record.task_id,'completed')
workspace=WorkspaceStore(store)
workspace.sync_task(record.task_id)
workspace.record_check(record.task_id,[1,2],[{'indices':[1],'type':'semantic_meaning','description':'可能存在语义差异','candidate_only':True}],status='checked')
`;
const env = {...process.env, SUBTITLE_LLM_USER_DATA_DIR: temp, SUBTITLE_LLM_DB_PATH: database, SUBTITLE_LLM_PYTHON: python,
  SUBTITLE_LLM_E2E_HIDDEN:'1', SUBTITLE_LLM_DESKTOP_CONFIG_DIR:path.join(temp, 'configs'), DEEPSEEK_API_KEY:'fake-e2e-only'};
for (const key of Object.keys(env)) if (/API_KEY|TOKEN|SECRET/.test(key) && key !== 'DEEPSEEK_API_KEY') delete env[key];
const seeded = spawnSync(python, ['-c', seed, database, temp], {cwd:root, env, encoding:'utf8'});
assert.equal(seeded.status, 0, seeded.stderr);
let app;
try {
  app = await electron.launch({executablePath: require('electron'), args:[root], env});
  const page = await app.firstWindow();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.locator('.material-card').first().click();
  await page.getByRole('button', {name:/问题回顾/}).click();
  await page.getByText('意思可能改变', {exact:false}).waitFor();
  await page.getByLabel('选择此项').check();
  await page.getByRole('button', {name:/保留所选当前译文/}).click();
  await page.getByText('当前没有待处理记录。', {exact:false}).waitFor();
  await page.getByRole('button', {name:'译文与交付'}).click();
  await page.getByRole('button', {name:'编辑', exact:true}).first().click();
  await page.getByRole('textbox', {name:'译文', exact:true}).fill('我们可以一起尝试。');
  await page.getByRole('button', {name:'保存修改'}).click();
  await page.getByRole('dialog', {name:'编辑字幕'}).waitFor({state:'hidden'});
  await page.getByText('我们可以一起尝试。', {exact:true}).waitFor();
  await page.getByRole('button', {name:/问题回顾/}).click();
  await page.getByText('尚未完成基于当前内容的语义检查。').waitFor();
  await page.getByRole('button', {name:'修改与用量'}).click();
  await page.getByRole('button', {name:'撤销此范围'}).click();
  await page.getByRole('button', {name:'译文与交付'}).click();
  await page.getByText('我们可以试试。', {exact:true}).waitFor();
  await app.evaluate(({dialog}, directory) => { dialog.showOpenDialog = async () => ({canceled:false, filePaths:[directory]}); }, temp);
  await page.getByRole('button', {name:'导出当前字幕'}).click();
  await page.getByRole('button', {name:'打开文件'}).waitFor();
  const subtitle = fs.readdirSync(temp).find(name => name.endsWith('.srt'));
  assert.ok(subtitle);
  assert.match(fs.readFileSync(path.join(temp, subtitle), 'utf8'), /我们可以试试/);
  await page.getByRole('button', {name:'新建翻译', exact:true}).click();
  await page.locator('.translate-options > summary').click();
  const originalModel = await page.locator('#modelSelect').inputValue();
  await page.locator('#modelSelect').selectOption('deepseek-v4-pro');
  // 本次选择不能自动写入默认。只有明确保存按钮才持久化。
  const settingsPath = path.join(temp, 'settings.json');
  const before = fs.existsSync(settingsPath) ? fs.readFileSync(settingsPath, 'utf8') : '';
  await page.locator('#translationMaxTokens').fill('4096');
  await page.locator('#targetLanguage').click();
  assert.equal(fs.existsSync(settingsPath) ? fs.readFileSync(settingsPath, 'utf8') : '', before);
  await page.locator('[data-workspace="materials"]').click();
  await page.getByRole('button', {name:'新建翻译', exact:true}).click();
  assert.equal(await page.locator('#modelSelect').inputValue(), originalModel);
  assert.equal(await page.locator('#translationMaxTokens').inputValue(), '');
  await page.locator('#modelSelect').selectOption('deepseek-v4-pro');
  await page.locator('#translationMaxTokens').fill('4096');
  await page.getByLabel('使用 Jev 语义检查', {exact:false}).uncheck();
  await page.getByRole('button', {name:'将模型选择明确保存为默认'}).click();
  await page.waitForFunction(() => window.subtitleLLM.getState().then(state => state.preferences.translationMaxTokens === 4096 && state.preferences.semanticQuality === 'off'));
  await page.locator('[data-workspace="materials"]').click();
  await page.locator('[data-workspace="materials"].is-active').waitFor();
  await page.screenshot({path:path.join('/tmp/subtitle-app-implementation', 'workspace-verified.png'), fullPage:true, animations:'disabled'});
  assert.deepEqual(errors, []);
  await app.close(); app = null;
  app = await electron.launch({executablePath: require('electron'), args:[root], env});
  const reopened = await app.firstWindow();
  await reopened.locator('.material-card').first().click();
  await reopened.getByRole('button', {name:'打开文件'}).waitFor();
  await reopened.getByText('我们可以试试。', {exact:true}).waitFor();
  assert.ok(fs.existsSync(database.replace('.sqlite3', '.before-workspace.sqlite3')));
  console.log('✓ 真实 SQLite + CLI + IPC：素材、复核、保护、编辑、撤销、导出、默认设置和重启恢复');
} catch (error) {
  if (app) { const page = await app.firstWindow(); await page.screenshot({path:'/tmp/subtitle-app-implementation/workspace-error.png'}); console.log((await page.locator('.material-workspace').innerText()).slice(-5000)); }
  throw error;
} finally {
  if (app) await app.close();
  fs.rmSync(temp, {recursive:true, force:true});
}
