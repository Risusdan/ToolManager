"""Start Menu shortcuts for installed exe tools (Windows only; no-op elsewhere)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

FOLDER = "ToolManager Tools"


def _safe_name(name: str) -> str:
    return "".join(c for c in name if c not in '<>:"/\\|?*').strip(" .") or "tool"


class Shortcuts:
    """Creates/removes .lnk files in <Start Menu>/Programs/ToolManager Tools.

    Callers keep the path returned by create() and pass it back to remove(), so a
    shortcut is only ever removed by the tool that created it.
    """

    def __init__(self, folder: Path | None = None) -> None:
        if folder is None and sys.platform == "win32":
            folder = (
                Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
                / FOLDER
            )
        self.folder = folder

    def create(self, name: str, target: Path, tool_id: str, existing: Path | None) -> Path | None:
        """Create or update the shortcut; returns its path. Best effort: never fails an install."""
        if self.folder is None:
            return None
        if existing is not None and self._ours(existing):
            lnk = existing
        else:
            lnk = self.folder / f"{_safe_name(name)}.lnk"
            if lnk.exists():  # another tool already uses this name
                lnk = self.folder / f"{_safe_name(name)} ({tool_id}).lnk"
        lnk.parent.mkdir(parents=True, exist_ok=True)

        def ps_quote(value: Path | str) -> str:
            return "'" + str(value).replace("'", "''") + "'"

        script = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
            f"{ps_quote(lnk)}); $s.TargetPath = {ps_quote(target)}; "
            f"$s.WorkingDirectory = {ps_quote(target.parent)}; $s.Save()"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                check=True,
                capture_output=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return lnk
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("could not create shortcut %s: %s", lnk, e)
            return None

    def remove(self, lnk: Path | None) -> None:
        if lnk is not None and self._ours(lnk):
            lnk.unlink(missing_ok=True)

    def _ours(self, lnk: Path) -> bool:
        return (
            self.folder is not None
            and lnk.suffix.lower() == ".lnk"
            and Path(os.path.abspath(lnk)).parent == Path(os.path.abspath(self.folder))
        )
