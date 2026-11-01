"""Diagnostics package: one zip a user sends along with a problem report.

Mainly for the customer edition, which has no Jira button: without it nobody but the user can
see their logs. Holds a summary (versions, folders, server, installed tools), the settings
files and the logs. Tokens live in the Windows credential store, never in these files; lines
that look like credentials are masked anyway, in case a library logged one.
"""

from __future__ import annotations

import datetime as dt
import re
import zipfile
from pathlib import Path

MAX_LOG_BYTES = 2 * 1024 * 1024  # per file: the end of a large log is the interesting part
_SECRET_RE = re.compile(
    r"(?i)(authorization:\s*(?:bearer|basic)\s+|(?:token|password|secret)[\"']?\s*[=:]\s*[\"']?)"
    r"[^\s\"',;]+"
)


def mask_secrets(text: str) -> str:
    return _SECRET_RE.sub(lambda m: m.group(1) + "***", text)


def _tail(path: Path) -> str:
    with path.open("rb") as f:
        size = f.seek(0, 2)
        f.seek(max(0, size - MAX_LOG_BYTES))
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    if size > MAX_LOG_BYTES:
        text = f"[... first {size - MAX_LOG_BYTES} bytes left out ...]\n" + text.split("\n", 1)[-1]
    return text


def build(dest_dir: Path, summary: dict[str, str], files: dict[str, Path | None],
          log_dirs: list[Path], now: dt.datetime | None = None) -> Path:
    """Write toolmanager-diagnostics-<time>.zip into dest_dir and return its path.

    files: name in the zip -> file to include (missing ones are skipped).
    log_dirs: every *.log* file in them goes to logs/<folder name>/.
    """
    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"toolmanager-diagnostics-{stamp}.zip"
    lines = [f"{key}: {value}" for key, value in summary.items()]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("summary.txt", mask_secrets("\n".join(lines) + "\n"))
        for name, path in files.items():
            if path is not None and path.is_file():
                zf.writestr(name, mask_secrets(path.read_text("utf-8", errors="replace")))
        seen: set[Path] = set()
        for folder in log_dirs:
            if not folder.is_dir() or folder.resolve() in seen:
                continue
            seen.add(folder.resolve())
            for log in sorted(folder.glob("*.log*")):
                if log.is_file():
                    zf.writestr(f"logs/{folder.parent.name}/{log.name}", mask_secrets(_tail(log)))
    return target
