"""Running Python script tools (see docs/DESIGN.md §5.3).

- Form mode: arguments come from a generated form; output is captured live for the log panel.
- Console mode (Windows, no declared arguments): a new console window, so interactive
  scripts that call input() keep working.
- GUI mode (gui = true): pythonw.exe, no console and no capture.
"""

from __future__ import annotations

import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path

from ..i18n import _
from .models import ArgSpec, ArgType

log = logging.getLogger(__name__)

WINDOWS = sys.platform == "win32"
FormValue = str | int | float | bool | None


class ArgError(Exception):
    """A form value is missing or invalid; the message is already translated."""


def _text(value: FormValue) -> str:
    return "" if value is None else str(value).strip()


def build_args(specs: tuple[ArgSpec, ...], values: Mapping[str, FormValue]) -> list[str]:
    """Turn form values into argv (see ArgSpec for the mapping). Empty optional values are
    left out so the script's own argparse defaults apply."""
    positional: list[str] = []
    options: list[str] = []
    for spec in specs:
        value = values.get(spec.name, spec.default)
        if spec.type is ArgType.BOOL:
            if value:
                options.append(spec.cli_flag)
            continue
        text = _text(value)
        if not text:
            if spec.required or spec.positional:
                raise ArgError(_("Please fill in \"{label}\"", label=spec.label))
            continue
        if spec.type is ArgType.INT:
            try:
                number = float(text)
            except ValueError:
                _bad(spec)
            if not number.is_integer():
                _bad(spec)
            text = str(int(number))
        elif spec.type is ArgType.FLOAT:
            try:
                float(text)
            except ValueError:
                _bad(spec)
        elif spec.type is ArgType.CHOICE and text not in spec.choices:
            _bad(spec)
        if spec.positional:
            positional.append(text)
        else:
            options += [spec.cli_flag, text]
    return options + positional


def _bad(spec: ArgSpec) -> None:
    raise ArgError(_("\"{label}\" has an invalid value", label=spec.label))


def script_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return env


class ScriptRun:
    """A captured run: output lines are collected on a thread and read by the UI."""

    def __init__(self, cmd: list[str], cwd: Path, log_file: Path | None = None) -> None:
        self.cmd = cmd
        self.cwd = cwd
        self.log_file = log_file
        self.returncode: int | None = None
        self.stopped = False
        self.started_at = time.time()
        self._lines: queue.Queue[str] = queue.Queue()
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {
            "start_new_session": True  # own process group, so stop() also ends children
        }
        self._proc = subprocess.Popen(
            cmd, cwd=cwd, env=script_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **flags,
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    @property
    def running(self) -> bool:
        return self.returncode is None

    def _read(self) -> None:
        out = None
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            out = self.log_file.open("w", encoding="utf-8")
            out.write("$ " + subprocess.list2cmdline(self.cmd) + "\n")
        try:
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                line = line.rstrip("\r\n")
                self._lines.put(line)
                if out:
                    out.write(line + "\n")
                    out.flush()
            self.returncode = self._proc.wait()
            if out:
                out.write(f"[exit code {self.returncode}]\n")
        finally:
            if out:
                out.close()

    def read_new(self, limit: int = 2000) -> list[str]:
        lines = []
        while len(lines) < limit:
            try:
                lines.append(self._lines.get_nowait())
            except queue.Empty:
                break
        return lines

    def stop(self) -> None:
        if not self.running:
            return
        self.stopped = True
        try:
            if WINDOWS:
                # The script may have started child processes: end the whole tree.
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self._proc.pid)],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(self._proc.pid, signal.SIGTERM)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("could not stop pid %s: %s", self._proc.pid, e)

    def wait(self, timeout: float | None = None) -> int | None:
        self._reader.join(timeout)
        return self.returncode


def launch_console(python: Path, entry: Path, cwd: Path) -> subprocess.Popen:
    """Windows: run in a new console window that stays open after the script ends."""
    return subprocess.Popen(["cmd.exe", "/k", str(python), str(entry)], cwd=cwd,
                            env=script_env(), creationflags=subprocess.CREATE_NEW_CONSOLE)


def launch_gui(pythonw: Path, entry: Path, cwd: Path) -> subprocess.Popen:
    flags = subprocess.DETACHED_PROCESS if WINDOWS else 0
    return subprocess.Popen([str(pythonw), str(entry)], cwd=cwd, env=script_env(),
                            creationflags=flags, close_fds=True)
