from __future__ import annotations

import json
from typing import Any

import typer

RESULT_EVENT_PREFIX = "SUBTITLE_LLM_RESULT "


def emit_result_event(command: str, **payload: Any) -> None:
    typer.echo(RESULT_EVENT_PREFIX + json.dumps({"command": command, **payload}, ensure_ascii=False, sort_keys=True))
