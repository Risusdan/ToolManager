"""Per-user data locations (see docs/DESIGN.md §2)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ToolManager"


def data_root() -> Path:
    """%LOCALAPPDATA%\\ToolManager on Windows; overridable via TOOLMANAGER_HOME for dev/tests."""
    override = os.environ.get("TOOLMANAGER_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME.lower()


def cache_dir() -> Path:
    return data_root() / "cache" / "catalog"


def tools_dir() -> Path:
    return data_root() / "tools"


def downloads_dir() -> Path:
    return data_root() / "downloads"


def trash_dir() -> Path:
    return data_root() / "trash"


def envs_dir() -> Path:
    return data_root() / "envs"


def logs_dir() -> Path:
    return data_root() / "logs"


def defaults_file() -> Path | None:
    """Department defaults next to the packaged exe; TOOLMANAGER_DEFAULTS overrides."""
    override = os.environ.get("TOOLMANAGER_DEFAULTS")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "defaults.toml"
    return None


def settings_file() -> Path:
    return data_root() / "settings.toml"
