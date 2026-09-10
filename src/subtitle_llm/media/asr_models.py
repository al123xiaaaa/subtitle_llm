"""ASR 模型注册表：可选模型 profile 的单一入口。

profile 数据存放在 config/desktop-contract.json 的 ``asrModels`` 字段，
Electron 桌面端（下拉选项与 YAML 生成）与 Python 共用同一份数据；
契约文件缺失时回退到内置 profile。tests/test_config_contract.py 锁定
两侧一致。

新增一个 ASR 模型 = 在契约里加一个 profile，不写代码。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from subtitle_llm.settings import ASRConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AsrModelProfile:
    id: str
    label: str
    description: str
    model_name: str
    hub: str = "ms"
    trust_remote_code: bool = False
    punc_model: str | None = None
    spk_model: str | None = None
    forced_aligner: str | None = None
    max_single_segment_time: int = 30000
    # code = en/zh（paraformer 系）；name = 英文/中文（Fun-ASR 系）
    language_style: str = "code"
    # 传给 model.generate 的额外参数（不同模型的签名差异在此吸收）
    generate_kwargs: dict[str, Any] = field(default_factory=dict)
    # 模型不返回时间戳：先用独立 fsmn-vad 切段，再逐段识别
    segment_via_vad: bool = False
    # funasr = FunASR Python SDK；transcribe-cpp = ggml CLI（whisper 系）
    backend: str = "funasr"
    gguf_repo: str | None = None
    gguf_file: str | None = None


# 契约缺失时的内置兜底，与 desktop-contract.json 的 asrModels 保持一致。
_BUILTIN_PROFILES: tuple[AsrModelProfile, ...] = (
    AsrModelProfile(
        id="fun-asr-nano",
        label="Fun-ASR-Nano",
        description="800M 多语大模型：中/英/日 + 方言口音，上下文理解强；首次下载约 3GB",
        model_name="FunAudioLLM/Fun-ASR-Nano-2512",
        hub="hf",
        trust_remote_code=True,
        max_single_segment_time=8000,
        language_style="name",
        generate_kwargs={"itn": True, "batch_size": 1},
        segment_via_vad=True,
    ),
    AsrModelProfile(
        id="paraformer-zh",
        label="Paraformer-zh",
        description="220M 轻量快速，中文精度最高；英文为附带支持",
        model_name="paraformer-zh",
        hub="ms",
        trust_remote_code=False,
        punc_model="ct-punc",
        spk_model="cam++",
        max_single_segment_time=8000,
        language_style="code",
        generate_kwargs={"use_itn": True, "batch_size_s": 60, "output_timestamp": True},
    ),
    AsrModelProfile(
        id="qwen3-asr",
        label="Qwen3-ASR",
        description="1.7B，52 语种高精度但 CPU 很慢；需附带强制对齐模型（约 7GB）",
        model_name="Qwen/Qwen3-ASR-1.7B",
        hub="hf",
        trust_remote_code=True,
        forced_aligner="Qwen/Qwen3-ForcedAligner-0.6B",
        max_single_segment_time=8000,
        language_style="name",
        generate_kwargs={"itn": True, "batch_size": 1},
        segment_via_vad=True,
    ),
    AsrModelProfile(
        id="whisper-turbo",
        label="Whisper large-v3-turbo",
        description="transcribe.cpp + Metal 加速，实测 59× 实时、英文精度最佳；100 语种（首次下载 845MB GGUF）",
        backend="transcribe-cpp",
        model_name="whisper-large-v3-turbo",
        hub="",
        language_style="iso",
        generate_kwargs={},
        gguf_repo="handy-computer/whisper-large-v3-turbo-gguf",
        gguf_file="whisper-large-v3-turbo-Q8_0.gguf",
    ),
    AsrModelProfile(
        id="whisper-large-v3",
        label="Whisper large-v3",
        description="transcribe.cpp + Metal 加速，LibriSpeech WER 1.82%；100 语种（首次下载 1.55GB GGUF）",
        backend="transcribe-cpp",
        model_name="whisper-large-v3",
        hub="",
        language_style="iso",
        generate_kwargs={},
        gguf_repo="handy-computer/whisper-large-v3-gguf",
        gguf_file="whisper-large-v3-Q8_0.gguf",
    ),
)

_BUILTIN_DEFAULT_ID = "fun-asr-nano"

_PROFILE_FIELDS = (
    "model_name",
    "hub",
    "trust_remote_code",
    "punc_model",
    "spk_model",
    "forced_aligner",
    "max_single_segment_time",
    "language_style",
    "generate_kwargs",
    "segment_via_vad",
    "backend",
    "gguf_repo",
    "gguf_file",
)


def _profiles_from_contract() -> tuple[dict[str, AsrModelProfile], str] | None:
    try:
        text = resources.files("subtitle_llm.config").joinpath("desktop-contract.json").read_text("utf-8")
        data = json.loads(text)
        profiles = {
            item["id"]: AsrModelProfile(**{k: v for k, v in item.items() if k in AsrModelProfile.__dataclass_fields__})
            for item in data["asrModels"]
        }
        default_id = data.get("defaultAsrModel") or next(iter(profiles))
        return profiles, default_id
    except Exception:
        logger.debug("读取 desktop-contract.json 的 asrModels 失败，使用内置 profile", exc_info=True)
        return None


def list_asr_models() -> list[AsrModelProfile]:
    loaded = _profiles_from_contract()
    if loaded is None:
        return list(_BUILTIN_PROFILES)
    return list(loaded[0].values())


def default_asr_model_id() -> str:
    loaded = _profiles_from_contract()
    if loaded is None:
        return _BUILTIN_DEFAULT_ID
    return loaded[1]


def get_asr_model(profile_id: str) -> AsrModelProfile:
    profiles = {profile.id: profile for profile in list_asr_models()}
    profile = profiles.get(profile_id)
    if profile is None:
        allowed = ", ".join(sorted(profiles))
        raise ValueError(f"未知 ASR 模型 profile：{profile_id!r}，可选：{allowed}")
    return profile


def resolve_asr_config(
    base: ASRConfig | None = None,
    *,
    profile: str | None = None,
    device: str | None = None,
) -> ASRConfig:
    """把 profile 解析成 ASRConfig。

    明确的优先级（高 → 低）：
    1. device 参数（临时覆盖，如 CLI --asr-device）；
    2. 指定了 profile（--asr-model）时，profile 整段胜出——
       配置文件的 asr 段被忽略（除显式设置的 device），避免
       default.yaml 的显式 model_name 把 profile 挤掉造成混合错配；
    3. 未指定 profile：base（YAML）原样生效；base 也没有时用默认 profile。
    """
    if profile is not None:
        selected = get_asr_model(profile)
        values: dict[str, Any] = {name: getattr(selected, name) for name in _PROFILE_FIELDS}
        if base is not None and "device" in base.model_fields_set:
            values["device"] = base.device
    elif base is not None:
        values = {name: getattr(base, name) for name in (*_PROFILE_FIELDS, "device")}
    else:
        selected = get_asr_model(default_asr_model_id())
        values = {name: getattr(selected, name) for name in _PROFILE_FIELDS}
    if device:
        values["device"] = device
    return ASRConfig(**values)
