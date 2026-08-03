"""ASR 后端抽象与 FunASR Python SDK 实现。

后端只产出带时间戳的识别片段（AsrCue），不碰 SRT 写出。
FunasrAsrBackend 通过 ``funasr.AutoModel`` 一次调用完成 VAD 分段 + 识别 + 标点恢复，
重建时间轴时把「断句」和「取时间戳」解耦：

- ``sentence_info`` 提供语义分段边界（哪些词属于同一句），但其 ``start/end`` 在长音频上
  会累积漂移，不能直接用作时间戳；
- 顶层 ``words`` + ``timestamp`` 提供原始音频的词级时间轴（无漂移），但不带语义断句。

因此主路径用 ``sentence_info`` 的段内子时间戳定位到顶层词序列的区间，再用顶层
``timestamp`` 取每段的起止毫秒——语义段不碎 + 时间准确。当 ``sentence_info`` 缺失或无法
对齐时，退化为按标点断句（``_build_cues_by_punctuation``）。

相比旧的 llama.cpp 二进制方案（ADR 0002），Python SDK 方案的优势：
- ``language`` 参数约束语言检测，从源头杜绝跨语言幻觉（英语视频不再冒出中文词）；
- ``punc_model`` 恢复标点，解决长段无标点无法断句的问题；
- 顶层词级 timestamp 提供原始音频时间轴，无需字数比例投影（方案 E）。
代价是重新引入 PyTorch 依赖；详见 docs/adr/0003。

**标签污染修复**：funasr 内部把含 SenseVoice 标签（``<|en|><|NEUTRAL|>`` 等）的
``result["text"]`` 直接传给 ct-punc，``split_words`` 把标签与首词粘连，导致 ``punc_array``
比 ``timestamp`` 短，``sentence_info`` 只覆盖音频前半段。``transcribe`` 在拿到 funasr
结果后用清洗后的 ``words``（纯词，不含标签）重新跑 ct-punc 重建 ``sentence_info``
（``_rebuild_sentence_info_if_possible``），确保覆盖全量音频。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from subtitle_llm.progress_events import ProgressEmitter
from subtitle_llm.settings import ASRConfig

logger = logging.getLogger(__name__)

# SenseVoice 输出的语言/事件标签，例如 <|en|>、<|EMO_UNKNOWN|>、<|nospeech|>
_SENSEVOICE_TAG_PATTERN = re.compile(r"<\|[^|]*\|>")
_SENTENCE_END_TOKENS = {".", "!", "?", "。", "！", "？"}
_NO_SPACE_BEFORE = {
    ".",
    ",",
    "!",
    "?",
    ";",
    ":",
    "%",
    "，",
    "。",
    "！",
    "？",
    "；",
    "：",
    "、",
    ")",
    "]",
    "}",
    "）",
    "】",
    "》",
    "」",
    "』",
}
_NO_SPACE_AFTER = {"(", "[", "{", "（", "【", "《", "「", "『"}


class AsrError(RuntimeError):
    """ASR 后端调用或解析失败。"""


@dataclass(frozen=True)
class AsrCue:
    """一段带时间戳的识别结果。"""

    start_ms: int
    end_ms: int
    text: str


class AsrBackend(Protocol):
    """ASR 后端协议：把音频转写成带时间戳的识别片段。"""

    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None,
        progress: ProgressEmitter | None = None,
    ) -> list[AsrCue]:
        ...


def clean_sensevoice_text(text: str) -> str:
    """清除 SenseVoice 输出中的特殊标记（如 <|en|>、<|EMO_UNKNOWN|>、<|nospeech|>）。"""
    return _SENSEVOICE_TAG_PATTERN.sub("", text).replace("▁", "").strip()


def _synthesize_words_from_chars(text: str, timestamps: list) -> list[str]:
    """把连续文本按字符级 timestamp 对齐合成 words。

    Qwen3-ASR 只返回 ``text``（自带标点）和字符级 ``timestamp``：时间戳只覆盖
    非标点字符（标点无声学表现，forced aligner 不对齐）。这里把标点并入前一个
    token（如 "面。"），使 words 与 timestamps 等长；句末标点随 token 结尾，
    ``_is_sentence_boundary_token`` 的 ``token[-1:]`` 判断正好能识别断句。
    段首孤立标点顺延并入后一个 token。对不齐（token 数 ≠ 时间戳数）时返回
    空列表，由调用方走其它回退路径。
    """
    tokens: list[str] = []
    pending_prefix = ""
    for ch in str(text):
        if ch.isspace():
            continue
        if ch in _PUNCTUATION_CHARS:
            if tokens:
                tokens[-1] += ch
            else:
                pending_prefix += ch
            continue
        tokens.append(pending_prefix + ch)
        pending_prefix = ""
    if pending_prefix and tokens:
        tokens[-1] += pending_prefix
    if len(tokens) != len(timestamps):
        logger.warning(
            "字符数与时间戳数不一致（%s vs %s），无法合成 words",
            len(tokens),
            len(timestamps),
        )
        return []
    return tokens


def _build_cues_by_sentence_alignment(
    words: object,
    timestamps: object,
    sentence_info: object,
) -> list[AsrCue]:
    """主路径：用 sentence_info 的语义边界对齐顶层词级时间轴重建字幕。

    思路是把「断句」和「取时间戳」解耦：

    - ``sentence_info`` 的段内子时间戳（``timestamp`` 字段）能精确定位到顶层
      ``timestamp`` 数组里的连续词索引区间，且各段首尾相接。用它决定在哪几个词之间切，
      得到语义完整的段落（不会在 ct-punc 误插的中间句号处断开）。
    - 每段的起止毫秒取自顶层 ``timestamp``（绝对音频时间轴，无 cam++ 累积漂移），
      而非 ``sentence_info`` 的 ``start/end``（长音频上会漂移）。

    ``sentence_info`` 的子时间戳只覆盖实词（标点 token 在子数组里通常缺失），因此段间
    的标点/未覆盖词会归入下一段，保证不丢内容。当 ``sentence_info`` 未覆盖到音频尾部
    （cam++ 分段可能提前结束）或子时间戳无法在顶层对齐时，未覆盖部分退化为按标点断句。

    返回空列表表示对齐失败，调用方应回退到 ``_build_cues_by_punctuation``。
    """
    if not isinstance(words, list) or not isinstance(timestamps, list):
        return []
    if not words or len(words) != len(timestamps):
        return []
    if not isinstance(sentence_info, list) or not sentence_info:
        return []

    # 顶层时间戳轴：建立「值 -> 索引」查找表。子时间戳里的元素形如 [start_ms, end_ms]。
    flat_timestamps: list[tuple[int, int]] = []
    for ts in timestamps:
        parsed = _parse_word_timestamp(ts)
        if parsed is None:
            return []
        flat_timestamps.append(parsed)
    if not flat_timestamps:
        return []

    # 把每个 sentence_info 段的子时间戳首/末元素映射到顶层词索引，得到连续区间。
    # 各段首尾相接（seg[i].last+1 == seg[i+1].first），因此直接以 first_idx..last_idx
    # 取区间即可覆盖全部词，不会丢内容。标点 token 若落在段首（cam++ 把句末标点算进下段），
    # 会在 _cue_from_word_range 里被当作前导标点剥离。
    segments: list[tuple[int, int]] = []
    last_end_idx = -1
    for seg in sentence_info:
        if not isinstance(seg, dict):
            continue
        sub_ts = seg.get("timestamp", [])
        if not isinstance(sub_ts, list) or not sub_ts:
            continue
        first_parsed = _parse_word_timestamp(sub_ts[0])
        last_parsed = _parse_word_timestamp(sub_ts[-1])
        if first_parsed is None or last_parsed is None:
            # 子时间戳格式异常，整体对齐失败，交由调用方回退。
            return []
        try:
            first_idx = flat_timestamps.index(first_parsed)
            last_idx = flat_timestamps.index(last_parsed)
        except ValueError:
            # 子时间戳不在顶层轴上，对齐失败。
            return []
        if last_idx < first_idx or first_idx <= last_end_idx:
            # 段顺序错乱（非单调/回退），放弃对齐避免错配。
            return []
        # cam++ 在长停顿处偶尔会产出「只含标点、无实词」的语义段（如 ".", ","）。
        # 这种段没有实质内容，不应单独成 cue；并入前段（扩展前段的 last_idx），
        # 避免下游翻译因孤立标点 cue 困惑（模型把翻译留空或把思考写进译文）。
        if segments and _range_is_punctuation_only(words, first_idx, last_idx):
            segments[-1] = (segments[-1][0], last_idx)
            last_end_idx = last_idx
            continue
        last_end_idx = last_idx
        segments.append((first_idx, last_idx))

    if not segments:
        return []

    cues: list[AsrCue] = []
    for start_idx, end_idx in segments:
        cue = _cue_from_word_range(words, flat_timestamps, start_idx, end_idx)
        if cue is not None:
            cues.append(cue)

    # sentence_info 未覆盖的尾部词（cam++ 分段可能提前结束）退化为按标点断句补齐。
    first_uncovered = segments[-1][1] + 1
    if first_uncovered < len(flat_timestamps):
        tail_cues = _build_cues_by_punctuation(
            words[first_uncovered : len(flat_timestamps)],
            timestamps[first_uncovered : len(flat_timestamps)],
        )
        cues.extend(tail_cues)

    return cues


def _rebuild_sentence_info(
    words: list,
    timestamps: list,
    punc_array: list,
) -> list[dict]:
    """用清洗后的 words+timestamp 重建 sentence_info，绕过 funasr 标签污染。

    funasr 内部把含 SenseVoice 标签（``<|en|><|NEUTRAL|>`` 等）的 ``result["text"]``
    直接传给 ct-punc，``split_words`` 把标签与首词粘连成一个 token，导致 ct-punc 的
    ``punc_array`` 比 ``timestamp`` 短。``timestamp_sentence_en`` 在 punc 耗尽处停止
    产出句段，``sentence_info`` 只覆盖音频前半段。

    本函数用清洗后的纯词文本重新调用 ``timestamp_sentence_en``——``punc_array`` 与
    ``timestamps`` 长度相等，重建的 ``sentence_info`` 覆盖全量音频。段内子时间戳元素
    直接引用自传入的 ``timestamps``，因此与 ``_build_cues_by_sentence_alignment``
    的 ``.index()`` 查找完全兼容。
    """
    from funasr.utils.timestamp_tools import timestamp_sentence_en

    clean_words = [clean_sensevoice_text(str(w)).strip() for w in words]
    clean_words = [w for w in clean_words if w]
    clean_text = " ".join(clean_words)
    return timestamp_sentence_en(punc_array, timestamps, clean_text)


def _cue_from_word_range(
    words: list,
    flat_timestamps: list[tuple[int, int]],
    start_idx: int,
    end_idx: int,
) -> AsrCue | None:
    """把顶层词序列的一个连续区间拼成一条 AsrCue，时间取区间首词起点、末词终点。"""
    range_words = [clean_sensevoice_text(str(words[i])).strip() for i in range(start_idx, end_idx + 1)]
    range_words = [word for word in range_words if word]
    if not range_words:
        return None
    # cam++ 有时把上一句的句末标点算进本段首位（如 ". Po request"），剥离前导纯标点，
    # 避免字幕以孤立句号开头；同时把起点时间戳前移到第一个实词。
    while (
        len(range_words) > 1
        and start_idx < end_idx
        and _is_punctuation_only(range_words[0])
    ):
        range_words.pop(0)
        start_idx += 1
    text = _format_word_tokens(range_words)
    text = clean_sensevoice_text(text).strip()
    if not text:
        return None
    start_ms, _ = flat_timestamps[start_idx]
    _, end_ms = flat_timestamps[end_idx]
    return AsrCue(start_ms=start_ms, end_ms=max(end_ms, start_ms + 1), text=text)


# 纯标点判断：仅由标点符号组成的 token（如 "."、","、"。。"），不含任何字母数字。
_PUNCTUATION_CHARS = set(".,!?;:，。！？；：、")


def _build_cues_by_punctuation(words: object, timestamps: object) -> list[AsrCue]:
    """退化路径：仅凭顶层 words/timestamp，按句末标点断句重建字幕。

    用作 ``sentence_info`` 缺失或无法对齐时的回退，也用于补齐 ``sentence_info`` 未覆盖的
    尾部。缺点是 ct-punc 会在话中间误插句号，导致每条都强以句号结尾、语义被切碎；但在
    没有 ``sentence_info`` 语义边界可用时，这是唯一能拿到时间轴的路径。
    """
    if not isinstance(words, list) or not isinstance(timestamps, list):
        return []
    if not words or len(words) != len(timestamps):
        return []

    tokens: list[tuple[str, int, int]] = []
    for word, timestamp in zip(words, timestamps, strict=True):
        text = clean_sensevoice_text(str(word)).strip()
        parsed_timestamp = _parse_word_timestamp(timestamp)
        if not text or parsed_timestamp is None:
            continue
        start_ms, end_ms = parsed_timestamp
        if end_ms < start_ms:
            continue
        tokens.append((text, start_ms, max(end_ms, start_ms + 1)))

    if not tokens:
        return []

    cues: list[AsrCue] = []
    current: list[tuple[str, int, int]] = []
    for index, token in enumerate(tokens):
        current.append(token)
        previous_text = tokens[index - 1][0] if index > 0 else None
        next_text = tokens[index + 1][0] if index + 1 < len(tokens) else None
        if _is_sentence_boundary_token(token[0], previous_text=previous_text, next_text=next_text):
            _append_word_timestamp_cue(cues, current)
            current = []
    _append_word_timestamp_cue(cues, current)
    # 过滤仅含标点的孤立片段（如尾部残留的 ","/"."），不构成有效字幕。
    return [cue for cue in cues if not _is_punctuation_only(cue.text)]


def _parse_word_timestamp(timestamp: object) -> tuple[int, int] | None:
    if isinstance(timestamp, dict):
        start = timestamp.get("start_time", timestamp.get("start"))
        end = timestamp.get("end_time", timestamp.get("end"))
        if start is None or end is None:
            return None
        return int(float(start) * 1000), int(float(end) * 1000)
    if isinstance(timestamp, (list, tuple)) and len(timestamp) >= 2:
        return int(float(timestamp[0])), int(float(timestamp[1]))
    return None


def _append_word_timestamp_cue(cues: list[AsrCue], tokens: list[tuple[str, int, int]]) -> None:
    if not tokens:
        return
    text = _format_word_tokens([token for token, _, _ in tokens])
    text = clean_sensevoice_text(text).strip()
    if not text:
        return
    cues.append(AsrCue(start_ms=tokens[0][1], end_ms=tokens[-1][2], text=text))


def _format_word_tokens(tokens: list[str]) -> str:
    text = ""
    for index, token in enumerate(tokens):
        previous_token = tokens[index - 1] if index > 0 else None
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        if not text:
            text = token
        elif _joins_previous_token(token, previous_token=previous_token, next_token=next_token, current_text=text):
            text += token
        else:
            text += " " + token
    return text


# CJK 表意字符（中/日/韩汉字）：字与字之间拼接不加空格
_CJK_CHAR_PATTERN = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")


def _joins_previous_token(
    token: str,
    *,
    previous_token: str | None,
    next_token: str | None,
    current_text: str,
) -> bool:
    if token == "." and previous_token and next_token and previous_token.isdigit() and next_token.isdigit():
        return True
    if token.isdigit() and previous_token and previous_token.isdigit():
        return True
    if previous_token == "." and token.isdigit() and len(current_text) >= 2 and current_text[-2].isdigit():
        return True
    if current_text[-1:] in {"'", "’"}:
        return True
    if token in _NO_SPACE_BEFORE:
        return True
    if token.startswith(("'", "’")):
        return True
    if current_text[-1:] in _NO_SPACE_AFTER:
        return True
    # 相邻 CJK 字符直接相连（如词级/字级 token "我" + "们" → "我们"）
    return bool(
        current_text[-1:]
        and token[:1]
        and _CJK_CHAR_PATTERN.match(current_text[-1])
        and _CJK_CHAR_PATTERN.match(token[0])
    )


def _patch_funasr_distribute_spk() -> None:
    """给 funasr 的 ``distribute_spk`` 打防御性补丁，绕过 None 时间戳崩溃。

    punc_array 与 timestamp 长度不一致时（funasr 会打 ``length mismatch between punc
    and timestamp`` 警告），``timestamp_sentence`` 会产出 ``start``/``end`` 为 None 的
    句子；``distribute_spk`` 对 None 直接做数值比较抛 ``TypeError``，整个转写前功尽弃。
    说话人标签（spk）本项目并不使用，这里把 None 起止填成占位值仅保证流程走完——
    后续 ``sentence_info`` 仍由 ``_rebuild_sentence_info_if_possible`` 用清洗后的
    words 重建，占位时间戳不会进入最终产物。
    """
    try:
        from funasr.auto import auto_model
    except ImportError:  # pragma: no cover - funasr 未安装时静默跳过
        return
    original = getattr(auto_model, "distribute_spk", None)
    if original is None or getattr(original, "_subtitle_llm_patched", False):
        return

    def _safe_distribute_spk(sentence_list, sd_time_list):
        last_end = 0
        for d in sentence_list:
            if d.get("start") is None or d.get("end") is None:
                logger.warning(
                    "funasr 句子时间戳为 None，填充占位值绕过 distribute_spk: %s",
                    str(d)[:120],
                )
                if d.get("start") is None:
                    d["start"] = last_end
                if d.get("end") is None:
                    d["end"] = d["start"]
            last_end = max(last_end, d["end"])
        return original(sentence_list, sd_time_list)

    _safe_distribute_spk._subtitle_llm_patched = True
    auto_model.distribute_spk = _safe_distribute_spk


def _is_sentence_boundary_token(
    token: str,
    *,
    previous_text: str | None,
    next_text: str | None,
) -> bool:
    if token == "." and previous_text and next_text and previous_text.isdigit() and next_text.isdigit():
        return False
    if token in _SENTENCE_END_TOKENS:
        return True
    return token[-1:] in _SENTENCE_END_TOKENS


def _is_punctuation_only(token: str) -> bool:
    """token 是否仅由标点组成（无字母数字），用于剥离段首孤立标点。"""
    return bool(token) and all(ch in _PUNCTUATION_CHARS for ch in token)


def _range_is_punctuation_only(words: list, start_idx: int, end_idx: int) -> bool:
    """顶层词序列的一个连续区间是否全由标点组成（无实词）。

    cam++ 在长停顿处偶尔会产出只含标点的语义段（ct-punc 插的 . , 等），这种段没有
    实质内容，应并入相邻段而非单独成 cue。
    """
    for i in range(start_idx, end_idx + 1):
        text = clean_sensevoice_text(str(words[i])).strip()
        if text and not _is_punctuation_only(text):
            return False
    return True


@dataclass
class FunasrAsrBackend:
    """基于 FunASR Python SDK 的 ASR 后端。

    通过 ``funasr.AutoModel`` 组合 SenseVoice（识别）+ fsmn-vad（分段）+
    ct-punc（标点恢复）+ cam++（说话人分离，触发 sentence_info 输出），
    一次调用产出带时间戳、带标点、语言受约束的识别片段。

    配置见 ASRConfig（模型名、max_single_segment_time、device 等）。
    首次运行会自动从 ModelScope/HuggingFace 下载模型（约 1GB）。
    """

    config: ASRConfig
    _model: object | None = field(default=None, init=False, repr=False)

    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None,
        progress: ProgressEmitter | None = None,
    ) -> list[AsrCue]:
        from funasr import AutoModel
        from funasr.utils.postprocess_utils import rich_transcription_postprocess

        _patch_funasr_distribute_spk()
        emit_asr_progress(progress, "load_asr", "加载 ASR", "正在加载 FunASR 模型")
        model = cast(Any, self._get_or_load_model(AutoModel))

        emit_asr_progress(progress, "load_asr", "加载 ASR", "正在运行 FunASR 识别")
        # language: 用户指定（如 "English"）则约束识别语言，杜绝跨语言幻觉；
        # None/"auto" 时由模型自动检测。
        lang = self._normalize_language(language)
        result = model.generate(
            input=str(audio_path),
            cache={},
            language=lang,
            use_itn=True,
            batch_size_s=60,
            output_timestamp=True,
        )
        emit_asr_progress(progress, "load_asr", "加载 ASR", "识别完成", status="done")

        if not result:
            logger.warning("FunASR 返回空结果: audio=%s", audio_path)
            return []

        res = result[0]
        words = res.get("words", [])
        timestamps = res.get("timestamp", [])
        sentence_info = res.get("sentence_info", [])

        # Qwen3-ASR 等模型不返回 words，只有 text（自带标点）+ 字符级 timestamp；
        # 逐字合成 words，让后续「按标点断句」路径可用。
        if not words and timestamps and res.get("text"):
            words = _synthesize_words_from_chars(res["text"], timestamps)

        # 治本：funasr 内部把含 SenseVoice 标签的 text 传给 ct-punc，标签与首词粘连导致
        # punc_array 比 timestamp 短，sentence_info 覆盖不全。用清洗后的 words 重建。
        sentence_info = self._rebuild_sentence_info_if_possible(
            model, words, timestamps, sentence_info
        )

        # 主路径：用 sentence_info 的语义边界对齐顶层词级时间轴——语义段完整、时间无漂移。
        aligned_cues = _build_cues_by_sentence_alignment(words, timestamps, sentence_info)
        if aligned_cues:
            logger.info(
                "FunASR sentence_info 对齐重建出 %s 个片段: audio=%s",
                len(aligned_cues),
                audio_path,
            )
            return aligned_cues

        # 退路径一：sentence_info 缺失/无法对齐但有词级时间戳，按标点断句（语义可能偏碎）。
        cues = _build_cues_by_punctuation(words, timestamps)
        if cues:
            logger.info(
                "FunASR 词级时间戳按标点重建出 %s 个片段: audio=%s",
                len(cues),
                audio_path,
            )
            return cues

        # 退路径二：连词级时间戳都没有，用 sentence_info 的 start/end（有漂移风险）兜底。
        cues = []
        if isinstance(sentence_info, list) and sentence_info:
            logger.warning(
                "FunASR 无法对齐词级时间戳，退用 sentence_info 句级时间: audio=%s",
                audio_path,
            )
            for seg in sentence_info:
                raw_text = seg.get("text", seg.get("sentence", ""))
                text = rich_transcription_postprocess(raw_text)
                text = clean_sensevoice_text(text)
                if not text:
                    continue
                cues.append(AsrCue(
                    start_ms=int(seg.get("start", 0)),
                    end_ms=int(seg.get("end", 0)),
                    text=text,
                ))
        else:
            # 退路径三：全都没有，整段一条（无时间戳信息）。
            logger.warning(
                "FunASR 未返回时间戳与 sentence_info，降级为整段一条: audio=%s", audio_path
            )
            text = clean_sensevoice_text(
                rich_transcription_postprocess(res.get("text", ""))
            )
            if text:
                cues.append(AsrCue(start_ms=0, end_ms=0, text=text))

        return cues

    def _rebuild_sentence_info_if_possible(
        self,
        model: object,
        words: list,
        timestamps: list,
        original_sentence_info: list,
    ) -> list:
        """用清洗后的 words 重建 sentence_info，绕过 funasr 标签污染。

        funasr 内部把含 SenseVoice 标签的 ``result["text"]`` 传给 ct-punc，标签与首词粘连
        导致 ``punc_array`` 比 ``timestamp`` 短，``sentence_info`` 覆盖不全。本方法用
        清洗后的纯词文本重新跑 ct-punc，拿到长度匹配的 ``punc_array`` 后重建 sentence_info。

        任何环节失败（无 punc_model、长度不匹配、ct-punc 报错）时静默回退到原始
        ``sentence_info``，保证不比现状更差。
        """
        # 守卫一：无词级时间戳，无法重建。
        if not isinstance(words, list) or not isinstance(timestamps, list):
            return original_sentence_info
        if not words or len(words) != len(timestamps):
            return original_sentence_info

        # 守卫二：配置未启用 ct-punc（punc_model 为 None），无法重新分词。
        punc_model = getattr(model, "punc_model", None)
        if punc_model is None:
            return original_sentence_info

        # 用清洗后的纯词文本重新跑 ct-punc，拿到与 timestamp 等长的 punc_array。
        clean_words = [clean_sensevoice_text(str(w)).strip() for w in words]
        clean_words = [w for w in clean_words if w]
        clean_text = " ".join(clean_words)
        if not clean_text:
            return original_sentence_info

        try:
            punc_kwargs = getattr(model, "punc_kwargs", {})
            punc_res = cast(
                Any, model
            ).inference(clean_text, model=punc_model, kwargs=punc_kwargs)
            punc_array = punc_res[0]["punc_array"]
        except (IndexError, KeyError, TypeError, RuntimeError) as exc:
            logger.warning("ct-punc 重建失败，回退原始 sentence_info: %s", exc)
            return original_sentence_info

        # ct-punc 在多段输入时返回 torch.Tensor（cat 拼接），单段时返回 list。
        # 统一转成 list，供 timestamp_sentence_en 按 index 访问。
        if hasattr(punc_array, "tolist"):
            punc_array = punc_array.tolist()
        if not isinstance(punc_array, list) or not punc_array:
            logger.warning("ct-punc 返回空 punc_array，回退原始 sentence_info")
            return original_sentence_info

        # 守卫三：重建后 punc_array 仍与 timestamp 长度不等（理论上清洗后不应发生），
        # 说明对齐仍有问题，不强行重建。
        if len(punc_array) != len(timestamps):
            logger.warning(
                "重建 punc_array 长度(%s) != timestamp 长度(%s)，回退原始 sentence_info",
                len(punc_array),
                len(timestamps),
            )
            return original_sentence_info

        rebuilt = _rebuild_sentence_info(words, timestamps, punc_array)
        if not rebuilt:
            return original_sentence_info

        original_count = len(original_sentence_info) if isinstance(original_sentence_info, list) else 0
        logger.info(
            "重建 sentence_info 绕过标签污染: %s 段（原 %s 段）",
            len(rebuilt),
            original_count,
        )
        return rebuilt

    def _get_or_load_model(self, auto_model_cls) -> object:
        """懒加载并缓存 AutoModel 实例（模型加载耗时，避免每次转写都重建）。"""
        if self._model is not None:
            return self._model

        cfg = self.config
        kwargs: dict[str, object] = {
            "model": cfg.model_name,
            "vad_model": "fsmn-vad",
            "vad_kwargs": {"max_single_segment_time": cfg.max_single_segment_time},
            "punc_model": cfg.punc_model,
            "spk_model": cfg.spk_model,
            "device": cfg.device,
            "disable_update": True,
            "disable_pbar": True,
        }
        # Fun-ASR-Nano / Qwen3-ASR 等需 trust_remote_code + HF hub
        if cfg.hub:
            kwargs["hub"] = cfg.hub
        if cfg.trust_remote_code:
            kwargs["trust_remote_code"] = True
        # Qwen3-ASR 需要 forced_aligner 才能输出字符级时间戳
        if cfg.forced_aligner:
            kwargs["forced_aligner"] = cfg.forced_aligner

        logger.info(
            "加载 FunASR 模型: model=%s punc=%s spk=%s device=%s",
            cfg.model_name, cfg.punc_model, cfg.spk_model, cfg.device,
        )
        self._model = auto_model_cls(**kwargs)
        return self._model

    @staticmethod
    def _normalize_language(language: str | None) -> str:
        """把 ASR 语言标识（如 "English"）转为 funasr 接受的形式（如 "en"）。

        funasr 的 language 参数接受语言代码或 "auto"。
        用户传入的可能是完整名称（来自 normalize_asr_language），这里映射回代码。
        """
        if not language:
            return "auto"
        mapping = {
            "english": "en", "chinese": "zh", "japanese": "ja",
            "korean": "ko", "cantonese": "yue", "auto": "auto",
        }
        return mapping.get(language.strip().lower(), language.strip().lower())


def emit_asr_progress(
    progress: ProgressEmitter | None,
    detail: str,
    label: str,
    message: str,
    *,
    status: str = "running",
) -> None:
    if progress is None:
        return
    progress.emit(
        stage="prepare_input" if progress.command == "translate" else "transcribe",
        detail=detail,
        status=status,
        label=label,
        message=message,
    )
