"""Per-user data locations (see docs/DESIGN.md §2)."""

from __future__ import annotations

import functools
import os
import sys
import tomllib
from pathlib import Path

APP_NAME = "ToolManager"
PORTABLE_DATA = "data"


def data_root() -> Path:
    """Where settings, installed tools, caches and logs live.

    TOOLMANAGER_HOME (dev/tests) > portable copy (defaults.toml says portable = true: a
    "data" folder next to the launcher, so an unzipped copy is self-contained and never
    mixes with an installed ToolManager) > %LOCALAPPDATA%\\ToolManager.
    """
    override = os.environ.get("TOOLMANAGER_HOME")
    if override:
        return Path(override)
    defaults = defaults_file()
    if defaults is not None:
        portable = _portable_root(str(defaults))
        if portable is not None:
            return portable
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


@functools.lru_cache(maxsize=8)
def _portable_root(defaults: str) -> Path | None:
    try:
        with open(defaults, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return Path(defaults).parent / PORTABLE_DATA if data.get("portable") is True else None


def defaults_file() -> Path | None:
    """Department defaults next to the packaged exe; TOOLMANAGER_DEFAULTS overrides."""
    override = os.environ.get("TOOLMANAGER_DEFAULTS")
    if override:
        return Path(override)
    # Started by the launcher: the app runs from app-x.y.z\, but the department file sits
    # next to the launcher so it survives updates (the launcher passes its own path).
    launcher = os.environ.get("TOOLMANAGER_LAUNCHER")
    if launcher:
        return Path(launcher).parent / "defaults.toml"
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "defaults.toml"
    return None


def settings_file() -> Path:
    return data_root() / "settings.toml"
