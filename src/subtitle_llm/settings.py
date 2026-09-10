from __future__ import annotations

import os
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigError(RuntimeError):
    """Raised when runtime configuration is invalid or incomplete."""


class ModelProvider(str, Enum):
    OPENAI = "openai"
    CUSTOM = "custom"
    GEMINI = "gemini"


class ModelConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: ModelProvider = Field(alias="type")
    model: str
    max_tokens: int = 8192
    api_key_env: str
    endpoint: str | None = None
    temperature: float = 0.5
    top_p: float = 1.0
    top_k: int | None = None
    repeat_penalty: float | None = None
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    n: int = 1
    stream: bool = False
    rate_limit: int | None = None
    request_timeout_seconds: float = 120.0
    max_retries: int = 6
    retry_delay_seconds: float = 60.0

    @field_validator("api_key_env")
    @classmethod
    def api_key_env_must_be_named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("api_key_env must not be empty")
        return value.strip()

    def require_api_key(self) -> str:
        value = os.getenv(self.api_key_env)
        if not value:
            raise ConfigError(
                f"缺少 API key 环境变量：{self.api_key_env} (model={self.model}, provider={self.provider.value})"
            )
        return value


class PipelineConfig(BaseModel):
    chunk_size: int = 34
    context_window_size: int = 4
    threads: int = 5
    ignore_subtitle_length: int = 4
    max_chars: int = 132
    max_duration: float = 10.0
    normalize_subtitles: Literal["auto", "always", "off"] = "auto"
    normalize_max_cue_chars: int = 84
    normalize_max_line_chars: int = 42
    normalize_max_duration: float = 7.0
    normalize_min_duration: float = 0.8
    semantic_translation: Literal["auto", "always", "off"] = "auto"
    semantic_output_granularity: Literal["cue", "unit"] = "cue"
    semantic_max_cues_per_unit: int = 6
    refine_translation: bool = False
    review_mode: Literal["auto", "tui"] = "auto"
    context_review: bool = False
    fallback_on_chunk_error: Literal["source", "abort"] = "source"

    @field_validator("chunk_size", "context_window_size", "threads")
    @classmethod
    def must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be positive")
        return value


class ASRConfig(BaseModel):
    """FunASR Python SDK 配置。

    默认值即默认 profile（fun-asr-nano）；可选 profile 见
    media/asr_models.py 注册表（数据来自 config/desktop-contract.json），
    通过 CLI --asr-model 或桌面端下拉选择。详见
    docs/adr/0003-asr-backend-migrate-to-funasr-sdk.md。
    首次运行自动下载模型。CPU 可跑。
    """

    # ASR 识别模型（ModelScope namespace 或 HuggingFace repo）
    model_name: str = "FunAudioLLM/Fun-ASR-Nano-2512"
    # 标点恢复模型；None 表示用模型自带标点（Fun-ASR-Nano/SenseVoice/Qwen3-ASR 自带）
    punc_model: str | None = None
    # 说话人分离模型；cam++ 会触发 sentence_info（带时间戳分段）输出
    spk_model: str | None = None
    # VAD 单段最长时长（毫秒）。控制分段粒度，避免超长段
    max_single_segment_time: int = 30000
    # 推理设备：cpu 或 cuda
    device: str = "cpu"
    # 模型 hub：modelscope/ms（国内快）或 hf（HuggingFace，海外）
    hub: str = "hf"
    # 某些模型（Fun-ASR-Nano / Qwen3-ASR）需要 trust_remote_code
    trust_remote_code: bool = True
    # 强制对齐模型；Qwen3-ASR 需要它才能输出字符级时间戳
    # （如 "Qwen/Qwen3-ForcedAligner-0.6B"），其它模型留空
    forced_aligner: str | None = None
    # 语言参数风格：code = en/zh（paraformer 系），name = 英文/中文（Fun-ASR 系）
    language_style: Literal["code", "name"] = "name"
    # 传给 model.generate 的额外参数（不同模型的签名差异在此吸收）
    generate_kwargs: dict[str, Any] = Field(
        default_factory=lambda: {"itn": True, "batch_size": 1}
    )
    # 模型不返回时间戳时（Fun-ASR-Nano / Qwen3-ASR）：先用独立 fsmn-vad 切段，
    # 再逐段识别，段时间戳即字幕时间轴
    segment_via_vad: bool = True


class AppConfig(BaseModel):
    config_version: str = "2"
    default_output_format: Literal[
        "source-first",
        "target-first",
        "target-only",
        "source-only",
        "bilingual",
    ] = "source-first"
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    summary_model: ModelConfig
    translation_model: ModelConfig
    asr: ASRConfig = Field(default_factory=ASRConfig)


def default_config_path() -> Path:
    return Path(str(resources.files("subtitle_llm.config").joinpath("default.yaml")))


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else default_config_path()
    try:
        raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return AppConfig.model_validate(raw)
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在：{config_path}") from exc
    except ValidationError as exc:
        raise ConfigError(f"配置文件校验失败：{exc}") from exc
