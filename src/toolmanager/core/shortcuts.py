"""Start Menu shortcuts for installed exe tools (Windows only; no-op elsewhere)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

FOLDER = "ToolManager Tools"


class Shortcuts:
    """Creates/removes <Start Menu>/Programs/ToolManager Tools/<name>.lnk."""

    def __init__(self, folder: Path | None = None) -> None:
        if folder is None and sys.platform == "win32":
            folder = (
                Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
                / FOLDER
            )
        self.folder = folder

    def _path(self, name: str) -> Path | None:
        if self.folder is None:
            return None
        safe = "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "tool"
        return self.folder / f"{safe}.lnk"

    def create(self, name: str, target: Path) -> None:
        """Best effort: a failed shortcut must never fail an install."""
        lnk = self._path(name)
        if lnk is None:
            return
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
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("could not create shortcut %s: %s", lnk, e)

    def remove(self, name: str) -> None:
        lnk = self._path(name)
        if lnk is not None:
            lnk.unlink(missing_ok=True)
