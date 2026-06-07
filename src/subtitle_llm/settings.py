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
    semantic_max_cues_per_unit: int = 6
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
    """FunASR ASR 引擎配置。

    默认使用 SenseVoiceSmall 模型，自带 VAD（fsmn-vad）和标点恢复（ct-punc）。
    """

    model: str = "iic/SenseVoiceSmall"
    vad_model: str = "fsmn-vad"
    punc_model: str = "ct-punc"
    spk_model: str | None = None  # 设为 "cam++" 启用说话人分离
    vad_max_segment_ms: int = 30000  # VAD 单段最大时长（毫秒）
    device: str | None = "cpu"

    @field_validator("vad_max_segment_ms")
    @classmethod
    def positive_integer(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be positive")
        return value


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
