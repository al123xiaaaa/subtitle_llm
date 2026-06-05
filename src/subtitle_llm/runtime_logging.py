from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

RUN_LOG_ENV = "SUBTITLE_LLM_RUN_LOG"


def configure_run_logging(command_name: str, log_dir: str | Path = Path("data/logs")) -> Path:
    log_path = _new_log_path(command_name, Path(log_dir))
    _install_file_handler(log_path)
    os.environ[RUN_LOG_ENV] = str(log_path.resolve())
    logging.getLogger(__name__).info(
        "运行日志已启动: command=%s cwd=%s log_file=%s",
        command_name,
        Path.cwd(),
        log_path,
    )
    return log_path


def configure_logging_from_env() -> Path | None:
    log_file = os.getenv(RUN_LOG_ENV)
    if not log_file:
        return None
    log_path = Path(log_file)
    _install_file_handler(log_path)
    logging.getLogger(__name__).info("子进程接入运行日志: log_file=%s", log_path)
    return log_path


def _new_log_path(command_name: str, log_dir: Path) -> Path:
    safe_command = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in command_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{timestamp}_{safe_command}_{os.getpid()}.log"


def _install_file_handler(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("subtitle_llm")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, "_subtitle_llm_run_handler", False):
            logger.removeHandler(handler)
            handler.close()

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler._subtitle_llm_run_handler = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
