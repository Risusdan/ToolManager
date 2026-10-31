"""Start ToolManager at login (Windows: HKCU Run key; per user, no admin rights).

The entry starts the launcher with --background, so ToolManager waits in the system tray
instead of opening its window.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ToolManager"


def command(launcher: Path) -> str:
    return subprocess.list2cmdline([str(launcher), "--background"])


def supported(launcher: Path | None) -> bool:
    return sys.platform == "win32" and launcher is not None


def is_enabled(registry=None) -> bool:
    winreg = registry or _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


def apply(enabled: bool, launcher: Path, registry=None) -> None:
    """Add or remove the login entry. Raises OSError if the registry refuses."""
    winreg = registry or _winreg()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command(launcher))
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
    log.info("start at login %s", "enabled" if enabled else "disabled")


def _winreg():
    import winreg

    return winreg
