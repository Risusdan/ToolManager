"""Only one ToolManager per user: a second start brings the running window to the front.

- An OS file lock in the data folder marks the running instance; the OS releases it if
  the process dies, so a crash never leaves a stale "already running".
- instance.json (next to the lock) tells a second start where to send "activate".
- Imports stay light: this runs before the UI framework is loaded, so a second start
  is near-instant.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

LOCK_FILE = "instance.lock"
INFO_FILE = "instance.json"
ACTIVATE_PATH = "/_tm/activate"
TOKEN_HEADER = "X-TM-Token"


class InstanceLock:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.token = secrets.token_hex(16)
        self.port: int | None = None
        self._handle = None

    @property
    def info_path(self) -> Path:
        return self.folder / INFO_FILE

    def acquire(self) -> bool:
        """True if this process is now the only instance."""
        self.folder.mkdir(parents=True, exist_ok=True)
        handle = open(self.folder / LOCK_FILE, "a+b")  # noqa: SIM115 - held for the app's life
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def publish(self, port: int) -> None:
        """Tell later starts how to reach this instance."""
        self.port = port
        data = {"port": port, "token": self.token, "pid": os.getpid()}
        tmp = self.info_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), "utf-8")
        os.replace(tmp, self.info_path)

    def reclaim(self) -> bool:
        """Take the lock back after a release that turned out to be premature."""
        if self._handle is None and self.acquire() and self.port is not None:
            self.publish(self.port)
        return self._handle is not None

    def release(self) -> None:
        """Give up being the instance (e.g. right before restarting into an update)."""
        if self._handle is None:
            return
        try:
            self.info_path.unlink(missing_ok=True)
            if sys.platform == "win32":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle, fcntl.LOCK_UN)
        except OSError as e:
            log.warning("could not release the instance lock: %s", e)
        finally:
            self._handle.close()
            self._handle = None


def activate_running(folder: Path, wait_seconds: float = 8.0) -> bool:
    """Ask the running instance to show its window. It may still be starting up, so its
    contact details are retried for a few seconds."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            info = json.loads((folder / INFO_FILE).read_text("utf-8"))
            request = urllib.request.Request(
                f"http://127.0.0.1:{int(info['port'])}{ACTIVATE_PATH}", method="POST",
                headers={TOKEN_HEADER: info["token"]}, data=b"",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                if response.status == 200:
                    return True
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def show_already_running_message() -> None:
    """The running instance did not answer: tell the user instead of starting a second one."""
    text = ("ToolManager 已在執行中，但沒有回應。\n\n"
            "請稍候再試；若持續發生，請從工作管理員結束 ToolManagerApp 後重新開啟。")
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "ToolManager", 0x40)  # MB_ICONINFORMATION
    else:
        print(text, file=sys.stderr)
