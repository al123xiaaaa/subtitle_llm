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


# 契约缺失时的内置兜底，与 desktop-contract.json 的 asrModels 保持一致。
_BUILTIN_PROFILES: tuple[AsrModelProfile, ...] = (
    AsrModelProfile(
        id="fun-asr-nano",
        label="Fun-ASR-Nano",
        description="800M 多语大模型：中/英/日 + 方言口音，上下文理解强；首次下载约 3GB",
        model_name="FunAudioLLM/Fun-ASR-Nano-2512",
        hub="hf",
        trust_remote_code=True,
        max_single_segment_time=30000,
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
        max_single_segment_time=30000,
        language_style="name",
        generate_kwargs={"itn": True, "batch_size": 1},
        segment_via_vad=True,
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

    profile 提供整套默认值；base（通常来自 YAML）里被显式设置的字段优先，
    未显式设置的字段由 profile 填充。device 参数是最高优先级的临时覆盖。
    """
    selected = get_asr_model(profile or default_asr_model_id())
    values: dict[str, Any] = {name: getattr(selected, name) for name in _PROFILE_FIELDS}
    if base is not None:
        for name in base.model_fields_set:
            # device 不属于 profile 数据（按机器选），但 base 显式设置的 device 要保留
            if name in _PROFILE_FIELDS or name == "device":
                values[name] = getattr(base, name)
    if device:
        values["device"] = device
    return ASRConfig(**values)
