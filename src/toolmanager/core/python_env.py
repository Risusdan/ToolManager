"""Isolated Python environments for script tools (see docs/DESIGN.md §5).

- The interpreter is always the one shipped with ToolManager (never downloaded): uv runs
  with UV_PYTHON_DOWNLOADS=never and --python <bundled interpreter>.
- Packages come from the department PyPI mirror, or offline from wheels/ in the tool zip.
- One environment per tool version, created on first run and rebuilt automatically when
  its inputs (requirements, wheels, interpreter, backend, index) change.

Layout: envs/<tool-id>/<version>/  (.tm-owned, env.json, venv/)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from ..i18n import _
from .trash import DeleteRefused, mark_owned, remove_owned

log = logging.getLogger(__name__)

Output = Callable[[str], None]
ENV_INFO = "env.json"
VENV = "venv"
REQUIREMENTS = "requirements.txt"
WHEELS = "wheels"
WINDOWS = sys.platform == "win32"


class EnvError(Exception):
    """User-facing failure; the message is already translated."""


@dataclass(frozen=True)
class PythonRuntime:
    python: Path
    uv: Path | None

    def version(self) -> Version:
        out = subprocess.run(
            [str(self.python), "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True, text=True, timeout=30, **_no_window(),
        )
        return Version(out.stdout.strip())


def _no_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}


def find_runtime(python_override: str = "", uv_override: str = "") -> PythonRuntime | None:
    """Bundled interpreter (runtime/ next to the app exe); in development the current one."""
    base = Path(sys.executable).parent / "runtime"
    if python_override:
        python = Path(python_override)
    elif getattr(sys, "frozen", False):
        python = base / ("python/python.exe" if WINDOWS else "python/bin/python3")
    else:
        python = Path(sys.executable)
    if not python.is_file():
        return None
    if uv_override:
        uv = Path(uv_override)
    elif (base / ("uv.exe" if WINDOWS else "uv")).is_file():
        uv = base / ("uv.exe" if WINDOWS else "uv")
    else:
        found = shutil.which("uv")
        uv = Path(found) if found else None
    return PythonRuntime(python=python, uv=uv if uv and uv.is_file() else None)


def venv_python(venv: Path, gui: bool = False) -> Path:
    if WINDOWS:
        return venv / "Scripts" / ("pythonw.exe" if gui else "python.exe")
    return venv / "bin" / "python"


def _stream(cmd: list[str], env: dict[str, str], output: Output) -> None:
    output("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True,
        encoding="utf-8", errors="replace", **_no_window(),
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for line in proc.stdout:
        line = line.rstrip("\r\n")
        output(line)
        tail = (tail + [line])[-15:]
    if proc.wait() != 0:
        raise EnvError(_("Preparing the Python environment failed (exit code {code}):",
                         code=proc.returncode) + "\n" + "\n".join(tail))


class EnvBackend(ABC):
    name = ""

    def __init__(self, runtime: PythonRuntime, index_url: str = "") -> None:
        self.runtime = runtime
        self.index_url = index_url

    @abstractmethod
    def create(self, venv: Path, requirements: Path | None, wheels: Path | None,
               output: Output) -> None:
        """Create venv and install requirements (offline from wheels when given)."""

    def _install_source(self, wheels: Path | None) -> list[str]:
        if wheels is not None:
            return ["--no-index", "--find-links", str(wheels)]
        return ["--index-url", self.index_url] if self.index_url else []


class UvBackend(EnvBackend):
    name = "uv"

    def __init__(self, runtime: PythonRuntime, index_url: str = "",
                 cache_dir: Path | None = None) -> None:
        super().__init__(runtime, index_url)
        if runtime.uv is None:
            raise EnvError(_("uv was not found next to ToolManager"))
        self.cache_dir = cache_dir

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            UV_PYTHON_DOWNLOADS="never",  # interpreters are never downloaded (GitHub is blocked)
            UV_NO_CONFIG="1",             # ignore developer uv.toml / pyproject settings
            UV_NO_PROGRESS="1",
        )
        env.pop("VIRTUAL_ENV", None)
        if self.cache_dir is not None:
            env["UV_CACHE_DIR"] = str(self.cache_dir)
        return env

    def create(self, venv: Path, requirements: Path | None, wheels: Path | None,
               output: Output) -> None:
        uv = str(self.runtime.uv)
        env = self._env()
        _stream([uv, "venv", "--python", str(self.runtime.python), str(venv)], env, output)
        if requirements is not None:
            _stream([uv, "pip", "install", "--python", str(venv_python(venv)),
                     "-r", str(requirements), *self._install_source(wheels)], env, output)


class VenvPipBackend(EnvBackend):
    """Fallback when uv cannot be used: standard venv + pip (slower, no shared cache)."""

    name = "venv"

    def create(self, venv: Path, requirements: Path | None, wheels: Path | None,
               output: Output) -> None:
        env = dict(os.environ)
        env.pop("VIRTUAL_ENV", None)
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        _stream([str(self.runtime.python), "-m", "venv", str(venv)], env, output)
        if requirements is not None:
            _stream([str(venv_python(venv)), "-m", "pip", "install", "-r", str(requirements),
                     *self._install_source(wheels)], env, output)


def make_backend(name: str, runtime: PythonRuntime, index_url: str,
                 cache_dir: Path | None) -> EnvBackend:
    if name == "venv" or runtime.uv is None:
        return VenvPipBackend(runtime, index_url)
    return UvBackend(runtime, index_url, cache_dir)


class EnvManager:
    def __init__(self, root: Path, backend: EnvBackend) -> None:
        self.root = root
        self.backend = backend
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._runtime_version: Version | None = None

    def env_dir(self, tool_id: str, version: str) -> Path:
        return self.root / tool_id / version

    def _fingerprint(self, tool_dir: Path) -> str:
        h = hashlib.sha256()
        h.update(f"{self.backend.name}\0{self.backend.runtime.python}\0"
                 f"{self.backend.index_url}\0".encode())
        req = tool_dir / REQUIREMENTS
        if req.is_file():
            h.update(req.read_bytes())
        wheels = tool_dir / WHEELS
        if wheels.is_dir():
            for whl in sorted(wheels.glob("*.whl")):
                h.update(f"{whl.name}:{whl.stat().st_size}\n".encode())
        return h.hexdigest()

    def is_ready(self, tool_id: str, version: str, tool_dir: Path) -> bool:
        env_dir = self.env_dir(tool_id, version)
        try:
            info = json.loads((env_dir / ENV_INFO).read_text("utf-8"))
        except (OSError, ValueError):
            return False
        return (info.get("fingerprint") == self._fingerprint(tool_dir)
                and venv_python(env_dir / VENV).is_file())

    def check_python(self, spec: str) -> None:
        if not spec:
            return
        try:
            specifier = SpecifierSet(spec)
        except InvalidSpecifier:
            raise EnvError(_("Invalid Python requirement '{spec}'", spec=spec)) from None
        if self._runtime_version is None:
            self._runtime_version = self.backend.runtime.version()
        if self._runtime_version not in specifier:
            raise EnvError(_("This tool needs Python {spec}, but ToolManager provides Python "
                             "{version}.", spec=spec, version=self._runtime_version))

    def ensure(self, tool_id: str, version: str, tool_dir: Path, python_spec: str,
               output: Output) -> Path:
        """Return the venv for this tool version, creating or rebuilding it if needed."""
        self.check_python(python_spec)
        key = f"{tool_id}/{version}"
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            if self.is_ready(tool_id, version, tool_dir):
                return self.env_dir(tool_id, version) / VENV
            env_dir = self.env_dir(tool_id, version)
            self._remove(env_dir)
            mark_owned(env_dir)
            req = tool_dir / REQUIREMENTS
            wheels = tool_dir / WHEELS
            output(_("Preparing the Python environment (first run, or its requirements "
                     "changed)..."))
            try:
                self.backend.create(env_dir / VENV, req if req.is_file() else None,
                                    wheels if wheels.is_dir() else None, output)
            except BaseException:
                self._remove(env_dir)
                raise
            (env_dir / ENV_INFO).write_text(
                json.dumps({"fingerprint": self._fingerprint(tool_dir)}), "utf-8")
            output(_("Python environment ready."))
            return env_dir / VENV

    def prune(self, keep: set[tuple[str, str]]) -> None:
        """Remove environments of tool versions that are no longer installed."""
        if not self.root.is_dir():
            return
        for tool_dir in self.root.iterdir():
            if not tool_dir.is_dir():
                continue
            for env_dir in tool_dir.iterdir():
                if env_dir.is_dir() and (tool_dir.name, env_dir.name) not in keep:
                    try:
                        self._remove(env_dir)
                    except EnvError as e:  # refused or in use: skip it, keep pruning others
                        log.info("left environment %s: %s", env_dir, e)

    def _remove(self, env_dir: Path) -> None:
        """Environments hold no user data and can always be rebuilt, so they are deleted
        directly, but still only when they carry our ownership marker."""
        if not env_dir.exists():
            return
        try:
            remove_owned(env_dir, [self.root])
        except DeleteRefused as e:
            raise EnvError(_("Refused to remove {path}: {error}", path=env_dir, error=e)) from e
        except OSError as e:
            raise EnvError(_("Files are in use. Close the tool and try again.")) from e
