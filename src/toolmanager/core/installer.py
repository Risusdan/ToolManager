"""Install, update, roll back and uninstall tools (see docs/DESIGN.md §4 and §6.1).

Layout under the tools root:

    <tool-id>/
      state.toml        current / previous version, auto-update flag, entry per version
      1.0.0/            one complete folder per kept version (contains .complete)
      1.1.0/
      .staging-x.y.z/   in-progress extraction, never used directly
    .trash/             folders being deleted (rename first, so a locked file fails cleanly)

Only the current and previous versions are kept, which is what makes one-click
rollback possible.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import tomllib
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

import tomli_w
from packaging.version import InvalidVersion, Version

from ..i18n import _
from .archive import UnsafeArchiveError, flatten_single_root, safe_extract
from .models import Release, Tool, ToolType
from .repository import Progress, Repository, RepositoryError
from .shortcuts import Shortcuts

log = logging.getLogger(__name__)

STATE_FILE = "state.toml"
COMPLETE_MARKER = ".complete"
STAGING_PREFIX = ".staging-"
TRASH = ".trash"


class InstallError(Exception):
    """User-facing failure; the message is already translated."""


@dataclass(frozen=True)
class InstalledTool:
    id: str
    name: str
    type: ToolType
    current: Version
    previous: Version | None = None
    auto_update: bool = False
    # version string -> entry file, for the versions on disk
    entries: dict[str, str] = field(default_factory=dict)


class UpdateState(StrEnum):
    NOT_INSTALLED = "not_installed"
    UP_TO_DATE = "up_to_date"
    UPDATE_AVAILABLE = "update_available"


@dataclass(frozen=True)
class ToolStatus:
    installed: InstalledTool | None
    latest: Release | None
    # True when any release newer than the installed one is marked important.
    important: bool = False

    @property
    def state(self) -> UpdateState:
        if self.installed is None:
            return UpdateState.NOT_INSTALLED
        if self.latest is not None and self.latest.version > self.installed.current:
            return UpdateState.UPDATE_AVAILABLE
        return UpdateState.UP_TO_DATE


class Installer:
    def __init__(
        self,
        repo: Repository,
        root: Path,
        downloads: Path,
        revision: Callable[[], str | None],
        client_version: Version,
        shortcuts: Shortcuts | None = None,
    ) -> None:
        self.repo = repo
        self.root = root
        self.downloads = downloads
        self.revision = revision
        self.client_version = client_version
        self.shortcuts = shortcuts or Shortcuts()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- queries -------------------------------------------------------------

    def installed(self, tool_id: str) -> InstalledTool | None:
        path = self.root / tool_id / STATE_FILE
        if not path.is_file():
            return None
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
            previous = data.get("previous")
            return InstalledTool(
                id=data["id"],
                name=data.get("name", data["id"]),
                type=ToolType(data["type"]),
                current=Version(data["current"]),
                previous=Version(previous) if previous else None,
                auto_update=bool(data.get("auto_update", False)),
                entries=dict(data.get("entries", {})),
            )
        except (tomllib.TOMLDecodeError, KeyError, ValueError, InvalidVersion) as e:
            log.warning("corrupt install state %s: %s", path, e)
            return None

    def list_installed(self) -> list[InstalledTool]:
        if not self.root.is_dir():
            return []
        found = (
            self.installed(p.name)
            for p in sorted(self.root.iterdir())
            if p.is_dir() and not p.name.startswith(".")
        )
        return [t for t in found if t is not None]

    def status(self, tool: Tool) -> ToolStatus:
        inst = self.installed(tool.id)
        important = inst is not None and any(
            r.important and r.version > inst.current for r in tool.releases
        )
        return ToolStatus(installed=inst, latest=tool.latest, important=important)

    def entry_path(self, inst: InstalledTool) -> Path:
        return self.root / inst.id / str(inst.current) / inst.entries[str(inst.current)]

    def auto_update_candidates(self, tools: tuple[Tool, ...]) -> list[Tool]:
        result = []
        for tool in tools:
            st = self.status(tool)
            if st.state is UpdateState.UPDATE_AVAILABLE and st.installed.auto_update:
                result.append(tool)
        return result

    # -- operations ----------------------------------------------------------

    def install(
        self, tool: Tool, release: Release | None = None, progress: Progress | None = None
    ) -> InstalledTool:
        """Install release (default: latest) and make it current. Also used for updates."""
        release = release or tool.latest
        if release is None:
            raise InstallError(_("This tool has no releases"))
        if tool.type is ToolType.INSTALLER:
            raise InstallError(_("Installer-type tools are opened, not installed"))
        self._check_compatible(release)

        with self._lock(tool.id):
            tool_dir = self.root / tool.id
            version_dir = tool_dir / str(release.version)
            if not (version_dir / COMPLETE_MARKER).is_file():
                staging = self._download_and_extract(tool, release, tool_dir, progress)
                if version_dir.exists():
                    self._discard(version_dir)
                staging.rename(version_dir)

            old = self.installed(tool.id)
            if old is None:
                previous = None
            elif old.current != release.version:
                previous = old.current
            else:
                previous = old.previous
            kept = {str(release.version)} | ({str(previous)} if previous else set())
            entries = {v: e for v, e in (old.entries if old else {}).items() if v in kept}
            entries[str(release.version)] = release.entry
            new = InstalledTool(
                id=tool.id,
                name=tool.name,
                type=tool.type,
                current=release.version,
                previous=previous,
                auto_update=old.auto_update if old else False,
                entries=entries,
            )
            self._write_state(new)
            self._prune(new)
            self._refresh_shortcut(new)
            log.info("installed %s %s", tool.id, release.version)
            return new

    def rollback(self, tool_id: str) -> InstalledTool:
        with self._lock(tool_id):
            inst = self.installed(tool_id)
            if inst is None or inst.previous is None:
                raise InstallError(_("No previous version to roll back to"))
            prev_dir = self.root / tool_id / str(inst.previous)
            if not (prev_dir / COMPLETE_MARKER).is_file():
                raise InstallError(_("No previous version to roll back to"))
            new = replace(inst, current=inst.previous, previous=inst.current)
            self._write_state(new)
            self._refresh_shortcut(new)
            log.info("rolled back %s to %s", tool_id, new.current)
            return new

    def uninstall(self, tool_id: str) -> None:
        with self._lock(tool_id):
            inst = self.installed(tool_id)
            tool_dir = self.root / tool_id
            if not tool_dir.exists():
                return
            try:
                self._discard(tool_dir, strict=True)
            except OSError:
                raise InstallError(
                    _("Files are in use. Close the tool and try again.")
                ) from None
            if inst is not None and inst.type is ToolType.EXE:
                self.shortcuts.remove(inst.name)
            log.info("uninstalled %s", tool_id)

    def set_auto_update(self, tool_id: str, enabled: bool) -> None:
        with self._lock(tool_id):
            inst = self.installed(tool_id)
            if inst is not None:
                self._write_state(replace(inst, auto_update=enabled))

    def launch(self, tool_id: str) -> subprocess.Popen:
        inst = self.installed(tool_id)
        if inst is None:
            raise InstallError(_("Tool is not installed"))
        if inst.type is ToolType.PYTHON:
            raise InstallError(_("Running Python tools is not supported yet"))
        entry = self.entry_path(inst)
        if not entry.is_file():
            raise InstallError(_("Program file not found: {path}", path=entry))
        try:
            return subprocess.Popen([str(entry)], cwd=entry.parent)
        except OSError as e:
            raise InstallError(_("Could not start the tool: {error}", error=e)) from e

    def open_installer(
        self, tool: Tool, release: Release | None = None, progress: Progress | None = None
    ) -> subprocess.Popen:
        """Download an installer-type release and start its installer. Nothing is tracked."""
        release = release or tool.latest
        if release is None:
            raise InstallError(_("This tool has no releases"))
        self._check_compatible(release)
        target = self.downloads / f"{tool.id}-{release.version}"
        with self._lock(tool.id):
            if not (target / COMPLETE_MARKER).is_file():
                staging = self._download_and_extract(tool, release, self.downloads, progress)
                if target.exists():
                    self._discard(target)
                staging.rename(target)
        entry = target / release.entry
        try:
            return subprocess.Popen([str(entry), *release.installer_args.split()], cwd=target)
        except OSError as e:
            raise InstallError(_("Could not start the tool: {error}", error=e)) from e

    def cleanup(self) -> None:
        """Remove leftovers from interrupted operations. Call once at startup."""
        shutil.rmtree(self.root / TRASH, ignore_errors=True)
        if self.root.is_dir():
            for staging in self.root.glob(f"*/{STAGING_PREFIX}*"):
                shutil.rmtree(staging, ignore_errors=True)

    # -- internals -----------------------------------------------------------

    def _lock(self, tool_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(tool_id, threading.Lock())

    def _check_compatible(self, release: Release) -> None:
        if release.min_client_version and release.min_client_version > self.client_version:
            raise InstallError(
                _("Requires ToolManager {version} or later. Please update ToolManager first.",
                  version=release.min_client_version)
            )

    def _download_and_extract(
        self, tool: Tool, release: Release, parent: Path, progress: Progress | None
    ) -> Path:
        revision = self.revision()
        if revision is None:
            raise InstallError(_("Catalog has not been synced yet"))
        parent.mkdir(parents=True, exist_ok=True)
        self.downloads.mkdir(parents=True, exist_ok=True)
        staging = parent / f"{STAGING_PREFIX}{tool.id}-{release.version}"
        shutil.rmtree(staging, ignore_errors=True)
        with tempfile.TemporaryDirectory(dir=self.downloads) as tmp:
            archive = Path(tmp) / "artifact.zip"
            try:
                self.repo.download_artifact(revision, release.artifact, archive, progress)
            except RepositoryError as e:
                raise InstallError(_("Download failed: {error}", error=e)) from e
            if not zipfile.is_zipfile(archive):
                raise InstallError(_("Downloaded file is not a valid zip"))
            try:
                safe_extract(archive, staging)
            except UnsafeArchiveError as e:
                shutil.rmtree(staging, ignore_errors=True)
                raise InstallError(str(e)) from e
        flatten_single_root(staging)
        if not (staging / release.entry).is_file():
            shutil.rmtree(staging, ignore_errors=True)
            raise InstallError(
                _("Package does not contain the entry file '{entry}'", entry=release.entry)
            )
        (staging / COMPLETE_MARKER).touch()
        return staging

    def _write_state(self, inst: InstalledTool) -> None:
        data = {
            "id": inst.id,
            "name": inst.name,
            "type": inst.type.value,
            "current": str(inst.current),
            "auto_update": inst.auto_update,
            "entries": inst.entries,
        }
        if inst.previous is not None:
            data["previous"] = str(inst.previous)
        path = self.root / inst.id / STATE_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(tomli_w.dumps(data).encode("utf-8"))
        os.replace(tmp, path)

    def _prune(self, inst: InstalledTool) -> None:
        keep = {str(inst.current)} | ({str(inst.previous)} if inst.previous else set())
        for child in (self.root / inst.id).iterdir():
            if child.is_dir() and not child.name.startswith(".") and child.name not in keep:
                try:
                    self._discard(child, strict=True)
                except OSError as e:
                    # Typically an old version still running; cleanup() retries next start.
                    log.info("could not remove %s yet: %s", child, e)

    def _discard(self, path: Path, strict: bool = False) -> None:
        """Rename into .trash (atomic; fails if files are locked), then delete."""
        trash = self.root / TRASH
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / f"{path.name}-{uuid.uuid4().hex[:8]}"
        try:
            path.rename(target)
        except OSError:
            if strict:
                raise
            shutil.rmtree(path, ignore_errors=True)
            return
        shutil.rmtree(target, ignore_errors=True)

    def _refresh_shortcut(self, inst: InstalledTool) -> None:
        if inst.type is ToolType.EXE:
            self.shortcuts.create(inst.name, self.entry_path(inst))
