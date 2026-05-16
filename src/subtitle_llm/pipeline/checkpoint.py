from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from subtitle_llm.domain import Subtitle
from subtitle_llm.pipeline.report import TranslationReport


class CheckpointMismatch(RuntimeError):
    pass


def sidecar_path(output_file: str | Path, suffix: str) -> Path:
    output_path = Path(output_file)
    return output_path.with_name(f"{output_path.stem}{suffix}")


def file_fingerprint(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class CheckpointStore:
    def __init__(
        self,
        checkpoint_file: str | Path,
        input_fingerprint: str,
        target_language: str,
        output_format: str,
        config_version: str,
    ):
        self.checkpoint_file = Path(checkpoint_file)
        self.input_fingerprint = input_fingerprint
        self.target_language = target_language
        self.output_format = output_format
        self.config_version = config_version

    def load(self) -> dict[str, Any]:
        if not self.checkpoint_file.exists():
            return {}
        try:
            data = json.loads(self.checkpoint_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"断点文件读取失败：{exc}") from exc

        expected = self.metadata()
        actual = data.get("metadata", {})
        for key, value in expected.items():
            if actual.get(key) != value:
                raise CheckpointMismatch(
                    f"断点文件不匹配：{key} expected={value!r}, actual={actual.get(key)!r}"
                )
        return data

    def save(self, subtitle: Subtitle, report: TranslationReport) -> None:
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "metadata": self.metadata(),
            "report": report.model_dump(),
            "entries": {
                str(entry.index): entry.to_dict()
                for entry in subtitle.entries
                if entry.translated_text.strip()
            },
        }
        tmp_path = Path(f"{self.checkpoint_file}.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, self.checkpoint_file)

    def metadata(self) -> dict[str, str]:
        return {
            "input_fingerprint": self.input_fingerprint,
            "target_language": self.target_language,
            "output_format": self.output_format,
            "config_version": self.config_version,
        }
