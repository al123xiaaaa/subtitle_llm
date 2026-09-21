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
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'subtitle-workspace-layout-'));
const screenshots = process.env.SUBTITLE_LLM_SCREENSHOT_DIR || path.join(temp, 'screenshots');
fs.mkdirSync(screenshots, {recursive: true});
const database = path.join(temp, 'translation-tasks.sqlite3');
const python = path.join(root, '.venv/bin/python');
const env = {...process.env, SUBTITLE_LLM_USER_DATA_DIR: temp, SUBTITLE_LLM_DB_PATH: database, SUBTITLE_LLM_PYTHON: python,
  SUBTITLE_LLM_E2E_HIDDEN: '1', SUBTITLE_LLM_DESKTOP_CONFIG_DIR: path.join(temp, 'configs'), DEEPSEEK_API_KEY: 'fake-e2e-only'};
for (const key of Object.keys(env)) if (/API_KEY|TOKEN|SECRET/.test(key) && key !== 'DEEPSEEK_API_KEY') delete env[key];
// 旧版本、长标题和多条双语字幕复现截图中的布局，不读取真实用户记录。
const seeded = spawnSync(python, ['-c', `
import sys
sys.path.insert(0, 'src')
from pathlib import Path
from subtitle_llm.pipeline.task_store import TranslationTaskStore
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.settings import AppConfig, ModelConfig
from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.workspace import WorkspaceStore
store = TranslationTaskStore(sys.argv[1])
folder = Path(sys.argv[2])
model = ModelConfig(type='custom', model='fixture-model', api_key_env='FAKE_KEY', endpoint='https://fake.invalid')
examples = [
 ("What's up guys and gals, welcome back to the Nerdcastle. Today in the world of indie games,", '大家好，欢迎回到 Nerdcastle。今天在独立游戏世界中，'),
 ("we will be taking a look at a title called The Tainted Lands.", '我们要来体验一款名为《污染之地》的游戏。'),
 ("It is actually a pretty spicy looking tactical RPG.", '这其实是一款看起来颇有特色的战术 RPG。'),
 ("The world is dark, but every decision you make shapes the story.", '这个世界虽然黑暗，但你的每一个决定都会影响故事的走向。'),
 ("You can build a party and explore the places beyond the city.", '你可以组建队伍，探索城市以外的地区。'),
]
for n, title in enumerate(['This New Grimdark Fantasy RPG Is Looking Insane! - The Tainted Lands', 'Designing for the quiet moments', '一段旅行，两个视角']):
 output = folder / f'result-{n}.srt'
 output.write_text('1\\n00:00:00,000 --> 00:00:04,000\\n示例字幕\\n')
 record = store.create_task(task_id=f'layout-{n}', input_display=title, working_directory=str(folder), source_subtitle_path=str(folder / f'{title}.srt'), normalized_input_fingerprint=f'layout-source-{n}', target_language='Chinese', source_language='en', output_format='source-first', output_file=str(output), config=AppConfig(summary_model=model,translation_model=model))
 entries = [SubtitleEntry(i+1, f'00:{(i*4)//60:02d}:{(i*4)%60:02d},000', f'00:{((i+1)*4)//60:02d}:{((i+1)*4)%60:02d},000', *examples[i%len(examples)]) for i in range(40)]
 report = TranslationReport(input_file=record.source_subtitle_path,output_file=str(output),context_file='',translation_complete=True,total_entries=40,accepted_entry_indices=list(range(1,41)))
 store.save_resume_state(record.task_id,Subtitle(entries),report)
 store.update_status(record.task_id,'completed')
 WorkspaceStore(store).sync_task(record.task_id)
`, database, temp], {cwd: root, env, encoding: 'utf8'});
assert.equal(seeded.status, 0, seeded.stderr);
let app;
try {
  app = await electron.launch({executablePath: require('electron'), args: [root], env});
  const page = await app.firstWindow();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const size = async (width, height) => {
    await app.evaluate(({BrowserWindow}, bounds) => BrowserWindow.getAllWindows()[0].setSize(...bounds), [width, height]);
    await page.waitForFunction(w => window.innerWidth === w, width);
  };
  const capture = async name => page.screenshot({path: path.join(screenshots, `${name}.png`), animations: 'disabled'});
  const noOverflow = async () => {
    const dimensions = await page.locator('.material-workspace').evaluate(el => ({scroll: el.scrollWidth, client: el.clientWidth}));
    assert.ok(dimensions.scroll <= dimensions.client + 1, JSON.stringify(dimensions));
  };
  await size(1440, 960);
  await page.locator('.material-card').first().waitFor();
  await capture('library');
  await page.locator('.material-card').filter({hasText: 'The Tainted Lands'}).click();
  await page.locator('.cue-row').first().waitFor();
  await noOverflow();
  await capture('result-wide');
  // 三列必须保持顺序且不重叠，长原文不能把译文挤出可见范围。
  const columns = await page.locator('.cue-row').first().evaluate(el => [...el.querySelectorAll('.cue-time, .cue-pair p, .cue-edit')].map(node => {const r=node.getBoundingClientRect(); return {left:r.left,right:r.right};}));
  assert.equal(columns.length, 4);
  assert.ok(columns.slice(1).every((column, index) => column.left >= columns[index].right));
  await page.getByRole('button', {name: /问题回顾/}).click();
  const historyToggle = page.getByLabel('查看全部历史');
  await historyToggle.check();
  const checkbox = await historyToggle.boundingBox();
  assert.ok(checkbox.width >= 14 && checkbox.width <= 20 && checkbox.height <= 20, JSON.stringify(checkbox));
  await historyToggle.uncheck();
  await page.locator('.review-card').first().waitFor();
  assert.equal(await page.locator('.review-cues').first().getAttribute('open'), null);
  await noOverflow();
  await capture('review-wide');
  await page.locator('.review-cues > summary').first().click();
  await page.locator('.review-cues .cue-row').first().waitFor();
  assert.equal(await page.locator('.review-cues .cue-row').first().isVisible(), true);
  await page.locator('.review-cues > summary').first().click();
  await page.getByRole('button', {name: '为当前版本补查'}).click();
  await page.getByRole('dialog', {name: '追加处理额度'}).waitFor();
  await capture('check-dialog');
  await page.getByRole('button', {name: '取消', exact: true}).click();
  await size(980, 720);
  await noOverflow();
  await capture('review-compact');
  await page.getByRole('button', {name: '译文与交付', exact: true}).click();
  await noOverflow();
  await capture('result-compact');
  const firstCue = await page.locator('.cue-row').first().boundingBox();
  assert.ok(firstCue.y + firstCue.height <= 720, '小窗口首屏应至少完整显示一条双语字幕');
  await page.getByRole('button', {name: '编辑', exact: true}).first().click();
  const modal = page.getByRole('dialog', {name: '编辑字幕'});
  await modal.waitFor();
  const form = await modal.locator('form').boundingBox();
  assert.ok(form.x >= 0 && form.x + form.width <= 980 && form.y >= 0 && form.y + form.height <= 720);
  await capture('edit-compact');
  await page.getByRole('button', {name: '取消', exact: true}).click();
  await page.getByRole('button', {name: '新建翻译', exact: true}).click();
  const semanticToggle = page.getByLabel('使用 Jev 语义检查', {exact: false});
  const semanticLabel = semanticToggle.locator('..');
  await semanticLabel.waitFor();
  const semanticBox = await semanticLabel.boundingBox();
  assert.ok(semanticBox.width >= 220, `语义检查说明不能被横向挤成竖排：${semanticBox.width}`);
  await capture('translate-compact');
  const drawerFits = async () => {
    const dimensions = await page.locator('.drawer-content').evaluate(el => ({scroll: el.scrollWidth, client: el.clientWidth}));
    assert.ok(dimensions.scroll <= dimensions.client + 1, '翻译配置不能横向溢出');
  };
  await drawerFits();
  await semanticToggle.uncheck();
  assert.equal(await semanticToggle.isChecked(), false);
  await semanticToggle.check();
  await page.locator('.translation-method > summary').click();
  const explanation = await page.locator('.translation-method p').boundingBox();
  assert.ok(explanation.width >= 220 && explanation.height < 150, '额度说明应保持正常横向阅读');
  await page.locator('.translate-options > summary').click();
  await page.locator('#modelSelect').selectOption('__custom__');
  await page.locator('#customModelInput').fill('fixture-provider/long-model-name-for-translation-layout-verification');
  await page.locator('#forceAsr').check();
  await page.locator('.drawer-content').evaluate(el => {el.scrollTop = 0;});
  await drawerFits();
  await capture('translate-expanded-compact');
  const modelBounds = await page.locator('#modelSelect').boundingBox();
  assert.ok(modelBounds.width >= 220, '翻译模型选择需保留完整可读宽度');
  await size(1728, 1040);
  await page.locator('.translate-options > summary').click();
  await page.locator('.translation-method > summary').click();
  await page.locator('.drawer-content').evaluate(el => {el.scrollTop = 0;});
  await drawerFits();
  await capture('translate-wide');
  await page.locator('.translate-options > summary').click();
  await page.locator('.drawer-content').evaluate(el => {el.scrollTop = 0;});
  await drawerFits();
  await capture('translate-expanded-wide');
  await page.locator('#muxSubtitle').scrollIntoViewIfNeeded();
  const muxBounds = await page.locator('#muxSubtitle').boundingBox();
  assert.ok(muxBounds.width >= 200, '独立封装工具的文件路径不能被双列布局挤窄');
  await capture('translate-mux');
  assert.deepEqual(errors, []);
  console.log('✓ 素材页布局：长标题、多条字幕、复选框、折叠上下文、弹窗、1440/980 窗口无横向溢出；新建翻译抽屉的说明、开关、展开设置和 MKV 工具可正常阅读');
} finally {
  if (app) await app.close();
  fs.rmSync(temp, {recursive: true, force: true});
}
