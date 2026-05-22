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
    model: str = "Qwen/Qwen3-ASR-1.7B"
    forced_aligner: str = "Qwen/Qwen3-ForcedAligner-0.6B"
    max_audio_length: int = 180
    device: str | None = "cpu"
    cache_dir: str | None = None
    prefer_local_cache: bool = True
    subtitle_gap_seconds: float = 0.45
    subtitle_max_chars: int = 84
    subtitle_max_duration: float = 7.0

    @field_validator("max_audio_length", "subtitle_max_chars")
    @classmethod
    def positive_integer(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be positive")
        return value

    @field_validator("subtitle_gap_seconds", "subtitle_max_duration")
    @classmethod
    def positive_float(cls, value: float) -> float:
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
