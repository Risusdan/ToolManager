"""Python environment abstraction for script tools (see docs/DESIGN.md §5).

Implementations land in P2; the interface is fixed now so the runner and UI can
be written against it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class EnvBackend(ABC):
    def __init__(self, python_exe: Path, index_url: str = "") -> None:
        # Always the bundled interpreter: downloading interpreters is not allowed.
        self.python_exe = python_exe
        self.index_url = index_url

    @abstractmethod
    def create(self, env_dir: Path, requirements: Path | None, wheels_dir: Path | None) -> None:
        """Create env_dir and install requirements, offline from wheels_dir when given."""

    @abstractmethod
    def python_in(self, env_dir: Path, gui: bool = False) -> Path:
        """Interpreter inside env_dir (pythonw.exe when gui=True on Windows)."""
