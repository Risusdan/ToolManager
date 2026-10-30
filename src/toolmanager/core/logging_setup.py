"""File logging under %LOCALAPPDATA%\\ToolManager\\logs, kept for 7 days."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler

from . import paths


def setup_logging(level: int = logging.INFO) -> None:
    log_dir = paths.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        log_dir / "toolmanager.log", when="midnight", backupCount=7, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler())
