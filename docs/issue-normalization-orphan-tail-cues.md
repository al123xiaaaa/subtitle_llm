# 归一化断句产生「孤儿尾」cue 的问题描述

## 1. 发现来源

- 日志:`data/logs/20260911_183152_translate_13450.log`
- 任务:2026-09-11 18:31:52–18:40:51,YouTube URL 翻译(`force_asr=True`,`review=tui`,模型 `gemini-3.8-flash-high`,经本地代理 `127.0.0.1:8317`)
- 规模:18 个翻译片段,源字幕 884 条 → 成品 852 条
- 成品:`data/output/AI Infrastructure Explained (GPUs, vLLM, and LLM-D).zh.srt`
- 归一化映射表:`data/output/AI Infrastructure Explained (GPUs, vLLM, and LLM-D).zh_normalization_map.json`

## 2. 现象:TUI 复核中的固定操作模式

本次任务有 10/18 个片段因质量检查命中进入 TUI 复核,全部 `round=1` 结束,`selected_for_retranslation=0`,`failed_chunks=0`。

用户在复核中的全部操作只有两类:**32 次「合并当前行与下一行」+ 10 次「接受全部」**;文本编辑 0 次、重译 0 次、跳过 0 次。每次「接受全部」之前都伴随着若干次合并,合并是复核中唯一的实质性交互。

### 2.1 合并点明细

| 复核片段 | 时间 | 合并的 cue 对(全局编号) |
|---|---|---|
| chunk 5 | 18:37:37–18:38:11 | [287,288] [295,296] [292,293] [270,271] [260,261] |
| chunk 3 | 18:38:16–18:38:23 | [166,167] [163,164] [152,153] [157,158] |
| chunk 4 | 18:38:36–18:38:41 | [224,225] [217,218] [194,195] |
| chunk 2 | 18:38:48 | [105,106] |
| chunk 6 | 18:38:59–18:39:01 | [313,314] [307,308] |
| chunk 10 | 18:39:10–18:39:20 | [514,515] [508,509] [537,538] [542,543] |
| chunk 9 | 18:39:27–18:39:43 | [501,502] [497,498] [473,474] [475,476] [478,479] |
| chunk 14 | 18:39:50–18:40:02 | [715,716] [712,713] [721,722] [742,743] |
| chunk 15 | 18:40:09 | [800,801] |
| chunk 1 | 18:40:39–18:40:48 | [44,45] [3,4] [13,14] |

### 2.2 被合并 cue 对的共同特征

对 32 对合并点在归一化源字幕(`*.normalized.en.srt`)中的形态统计:

- **A cue**:时长中位数 4.35s(全部条目整体中位数 3.60s),文本以非句尾形态结束(无终止标点,常断在短语中间),如 `...waiting to be poured into the`
- **B cue**:**32/32 全部精确为 0.80s**,文本为 1–2 个词的句子尾巴并带终止标点,如 `GPU.` `next.` `seconds.` `memory.` `PyTorch.` `model.` `it.` `want.` `today.`
- **时间关系**:B 紧跟 A 之后,gap 为 +0.05s ~ +0.11s;合并后 884 → 852,减少的 32 条全部来自合并

典型样本:

```
A(4.30s): And VLLM then reads the model, waits off the disk, and loads them into the GPU
B(0.80s): memory.

A(4.87s): Like between reading a prompt and handing back the output, that's what we'll see
B(0.80s): next.

A(5.03s): If you're an SRE, a systems administrator, or a DevOps engineer, this course is for
B(0.80s): you.
```

## 3. 产生机制

### 3.1 流水线各环节数据

1. **ASR**:`transcribe-cli`(whisper-large-v3-turbo,`--timestamps segment`)产出 643 条 cue,平均 14.55 词/条,存在大量超出字幕可读长度的长 cue(日志 18:36:31,`cues=643`)。
2. **归一化**:`TranslationService` 对 ASR 来源输入强制启用归一化(`service.py:183`,`from_asr` → mode `"always"`)。映射表记录:`applied=true`,`original_entries=643`,`normalized_entries=884`。**cue 数量的净增长(643 → 884)全部发生在归一化环节,而非 ASR 环节。**
3. **语义翻译单元**:归一化结果之上,规划器进一步组成 553 个语义单元(`multi_cue_units=224`)。

### 3.2 归一化内部的行为

相关代码:`src/subtitle_llm/pipeline/normalization.py`

- `normalize_subtitle` 用 pysbd 按**句子**重建条目,随后对超过显示预算的句子调用 `split_cue_parts`(`normalization.py:377`)切分。
- 默认预算(`NormalizationOptions`,`normalization.py:38-47`):`max_cue_chars=84`,`max_duration_seconds=7.0`,`min_duration_seconds=0.8`,`gap_ms=40`。
- `split_cue_parts` 的切分方式是按词边界**贪心累加**到 `target_chars`(84)为止,余下部分成为最后一段。**算法不做段间再平衡**,当句子总长刚好超过预算时,最后一段只剩 1–2 个词。
- 每段的结束时间由文本位置线性插值得到(`time_for_position`);`normalization.py:169` 强制执行 `min_duration_seconds` 下限:`cue_end_ms = max(cue_end_ms, cue_start_ms + 800)`。1–2 个词的真实时长(约 0.2–0.5s)被统一拉长到精确 0.80s,再经 `adjust_timings`(`normalization.py:421`)保证与上一段至少 40ms 间隔。

### 3.3 因果验证

用 `*.zh_normalization_map.json` 核对合并对,每对 A|B 的 `original_indices` 均重叠于同一条原始 ASR cue,B 恰为该 cue 文本的末尾残段:

| 合并对 | A 映射的原始 cue | B 映射的原始 cue | B 文本 |
|---|---|---|---|
| [287,288] | [196, 197] | [197] | `GPU.` |
| [295,296] | [200, 201] | [201] | `next.` |
| [800,801] | [586, 587] | [587] | `model.` |
| [44,45] | [28, 29] | [29] | `it.` |
| [152,153] | [101, 102] | [102] | `want.` |

即:一条 ASR 长 cue 在归一化中被按 84 字符预算切断,句尾残段被 0.8s 下限铸造成「1–2 词 + 0.80s」的独立条目。TUI 复核中用户合并掉的 32 对,与归一化造出的这种条目一一对应。

## 4. 规模与残留

- 对 884 条归一化条目整体扫描(判据:时长 ≤0.85s 且词数 ≤2),共 **97 条**孤儿尾,占 11%;所有命中条目时长均精确为 0.80s。
- 本次复核覆盖其中 32 条(落在被抽查的 10 个片段内),其余 65 条未经人工处理。
- 对成品 `*.zh.srt` 用同一判据扫描,仍残留 **13 条**孤儿尾(如 `0.80s | CPU. CPU。`、`0.80s | of code. 代码。`),即未抽查片段中的孤儿尾原样流入最终字幕。

## 5. 与质量门标记的交叠

复核片段的质量标记中,有一部分直接落在孤儿尾条目上:

- chunk 15 被标记的 `[800,801]` 即一对孤儿尾(见 2.1 表);
- 「not primarily in the target language」类标记与「仅含标点」类标记,其对象多为 1–2 词的孤立条目(如 `GPU.` 译文为 `GPU。`、`it.` 等),这类条目单独进入翻译与检测流程。

## 6. 复现路径

1. 任一 YouTube URL + `force_asr=True` + `--review` 触发本次相同链路(whisper `--timestamps segment` → `normalize_subtitle` mode=always → 语义 cue 翻译 → TUI 复核);
2. 或在任意 ASR 来源字幕上直接调用 `normalize_subtitle`,默认 `NormalizationOptions` 即可产生同形态条目:句子总长略超 84 字符时,尾段为 1–2 词并被拉长至 0.80s。
