"""Install, update, roll back and uninstall tools (see docs/DESIGN.md §4, §6.1, §6.3).

Layout under the tools root:

    <tool-id>/                .tm-owned marks it as created by ToolManager
      state.toml              current / previous version, auto-update flag, entry per version
      1.0.0/                  one complete folder per kept version
        .complete             written last: the folder is a finished install
        .tm-files             size + mtime of every installed file, to spot user files later
      1.1.0/
      .staging-<id>-x.y.z/    in-progress extraction, never used directly

Deletion rules (the user's files must never disappear by accident):
- folders are never deleted in place; they are moved into the Trash (kept 7 days)
- only folders carrying a ToolManager marker inside the managed roots can be moved
- an old version that contains files the user added or changed is *kept* and
  reported, until the user explicitly discards it
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
import tomllib
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

import tomli_w
from packaging.version import InvalidVersion, Version

from ..i18n import _
from .archive import UnsafeArchiveError, flatten_single_root, safe_extract
from .manifest import TOOL_ID_RE
from .models import Release, Tool, ToolType
from .repository import Progress, Repository, RepositoryError
from .shortcuts import Shortcuts
from .trash import (
    COMPLETE_MARKER,
    OWNER_MARKER,
    DeleteRefused,
    Trash,
    is_owned,
    mark_owned,
    remove_owned,
)

log = logging.getLogger(__name__)

STATE_FILE = "state.toml"
FILES_MANIFEST = ".tm-files"
STAGING_PREFIX = ".staging-"
MARKERS = frozenset({OWNER_MARKER, COMPLETE_MARKER, FILES_MANIFEST})
# Staging folders younger than this may belong to another running instance.
STALE_STAGING_SECONDS = 3600


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
    shortcut: Path | None = None


@dataclass(frozen=True)
class RetainedVersion:
    """An old version folder kept because it holds files the user added or changed."""

    tool_id: str
    version: str
    path: Path
    user_files: tuple[str, ...]


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
    # Their important_note texts, newest first (may be empty even when important).
    important_notes: tuple[str, ...] = ()

    @property
    def state(self) -> UpdateState:
        if self.installed is None:
            return UpdateState.NOT_INSTALLED
        if self.latest is not None and self.latest.version > self.installed.current:
            return UpdateState.UPDATE_AVAILABLE
        return UpdateState.UP_TO_DATE


def write_files_manifest(folder: Path) -> None:
    files = {}
    for path in _walk_files(folder):
        rel = path.relative_to(folder).as_posix()
        st = path.stat()
        files[rel] = [st.st_size, st.st_mtime_ns]
    (folder / FILES_MANIFEST).write_text(json.dumps(files), encoding="utf-8")


def user_files(folder: Path) -> list[str]:
    """Files added or modified since install. Without a manifest, every file counts (safe side)."""
    try:
        recorded = json.loads((folder / FILES_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        recorded = {}
    changed = []
    for path in _walk_files(folder):
        rel = path.relative_to(folder).as_posix()
        st = path.stat()
        if recorded.get(rel) != [st.st_size, st.st_mtime_ns]:
            changed.append(rel)
    return sorted(changed)


def _walk_files(folder: Path):
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        base = Path(dirpath)
        # Never descend into links/junctions: their targets are not ours.
        dirnames[:] = [
            d for d in dirnames
            if not (base / d).is_symlink() and not os.path.isjunction(base / d)
        ]
        for name in filenames:
            if base == folder and name in MARKERS:
                continue
            yield base / name


class Installer:
    def __init__(
        self,
        repo: Repository,
        root: Path,
        downloads: Path,
        revision: Callable[[], str | None],
        client_version: Version,
        trash: Trash,
        shortcuts: Shortcuts | None = None,
    ) -> None:
        self.repo = repo
        self.root = root
        self.downloads = downloads
        self.revision = revision
        self.client_version = client_version
        self.trash = trash
        self.shortcuts = shortcuts or Shortcuts()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @property
    def managed_roots(self) -> list[Path]:
        return [self.root, self.downloads]

    # -- queries -------------------------------------------------------------

    def installed(self, tool_id: str) -> InstalledTool | None:
        if not TOOL_ID_RE.match(tool_id):
            return None
        path = self.root / tool_id / STATE_FILE
        if not path.is_file():
            return None
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
            if data["id"] != tool_id:
                raise ValueError(f"id {data['id']!r} does not match folder {tool_id!r}")
            previous = data.get("previous")
            shortcut = data.get("shortcut")
            return InstalledTool(
                id=tool_id,
                name=data.get("name", tool_id),
                type=ToolType(data["type"]),
                current=Version(data["current"]),
                previous=Version(previous) if previous else None,
                auto_update=bool(data.get("auto_update", False)),
                entries=dict(data.get("entries", {})),
                shortcut=Path(shortcut) if shortcut else None,
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
        newer_important = [
            r for r in tool.releases
            if inst is not None and r.important and r.version > inst.current
        ]
        return ToolStatus(
            installed=inst,
            latest=tool.latest,
            important=bool(newer_important),
            important_notes=tuple(r.important_note for r in newer_important if r.important_note),
        )

    def entry_path(self, inst: InstalledTool) -> Path:
        return self.root / inst.id / str(inst.current) / inst.entries[str(inst.current)]

    def auto_update_candidates(self, tools: tuple[Tool, ...]) -> list[Tool]:
        result = []
        for tool in tools:
            st = self.status(tool)
            if st.state is UpdateState.UPDATE_AVAILABLE and st.installed.auto_update:
                result.append(tool)
        return result

    def retained_versions(self, tool_id: str) -> list[RetainedVersion]:
        """Old version folders kept on disk because they hold user files."""
        inst = self.installed(tool_id)
        if inst is None:
            return []
        keep = self._kept_versions(inst)
        result = []
        for child in sorted((self.root / tool_id).iterdir()):
            if child.is_dir() and not child.name.startswith(".") and child.name not in keep:
                result.append(
                    RetainedVersion(tool_id, child.name, child, tuple(user_files(child)))
                )
        return result

    def user_files_of(self, tool_id: str) -> list[str]:
        """User files across every version folder of a tool (shown before uninstall)."""
        tool_dir = self.root / tool_id
        if not TOOL_ID_RE.match(tool_id) or not tool_dir.is_dir():
            return []
        result = []
        for child in sorted(tool_dir.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                result += [f"{child.name}/{f}" for f in user_files(child)]
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
            self._claim(tool_dir)
            version_dir = tool_dir / str(release.version)
            if not (version_dir / COMPLETE_MARKER).is_file():
                if version_dir.exists():
                    # A version folder without the completion marker was not made by us.
                    raise InstallError(
                        _("Folder {path} already exists and was not created by ToolManager. "
                          "Move it away and try again.", path=version_dir)
                    )
                staging = self._download_and_extract(tool, release, tool_dir, progress)
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
                shortcut=old.shortcut if old else None,
            )
            new = self._refresh_shortcut(new)
            self._write_state(new)
            self._prune(new)
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
            swapped = replace(inst, current=inst.previous, previous=inst.current)
            new = self._refresh_shortcut(swapped)
            self._write_state(new)
            log.info("rolled back %s to %s", tool_id, new.current)
            return new

    def uninstall(self, tool_id: str) -> None:
        """Move the whole tool folder to the Trash (restorable for the retention period)."""
        with self._lock(tool_id):
            inst = self.installed(tool_id)
            if inst is None:
                raise InstallError(_("Tool is not installed"))
            self._to_trash(
                self.root / tool_id, label=f"{inst.name} {inst.current}", reason="uninstalled",
                tool_id=tool_id,
            )
            self.shortcuts.remove(inst.shortcut)
            log.info("uninstalled %s", tool_id)

    def discard_version(self, tool_id: str, version: str) -> None:
        """User-confirmed: move a retained old version (with its user files) to the Trash."""
        with self._lock(tool_id):
            inst = self.installed(tool_id)
            if inst is None or version in self._kept_versions(inst):
                raise InstallError(_("This version is in use and cannot be removed"))
            self._to_trash(
                self.root / tool_id / version,
                label=f"{inst.name} {version}",
                reason="old version",
                tool_id=tool_id,
            )

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
            # Python tools need their environment first: the UI runs them from the Run page.
            raise InstallError(_("Python tools are started from their Run page"))
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
                if target.exists():
                    raise InstallError(
                        _("Folder {path} already exists and was not created by ToolManager. "
                          "Move it away and try again.", path=target)
                    )
                staging = self._download_and_extract(tool, release, self.downloads, progress)
                staging.rename(target)
        entry = target / release.entry
        try:
            return subprocess.Popen([str(entry), *release.installer_args.split()], cwd=target)
        except OSError as e:
            raise InstallError(_("Could not start the tool: {error}", error=e)) from e

    def cleanup(self) -> None:
        """Purge expired Trash items and stale staging folders. Call once at startup."""
        try:
            purged = self.trash.purge_expired()
            if purged:
                log.info("purged %d expired trash item(s)", purged)
        except OSError as e:
            log.warning("trash purge incomplete: %s", e)
        cutoff = time.time() - STALE_STAGING_SECONDS
        for parent in (self.root, self.downloads):
            if not parent.is_dir():
                continue
            stagings = [*parent.glob(f"*/{STAGING_PREFIX}*"), *parent.glob(f"{STAGING_PREFIX}*")]
            for staging in stagings:
                try:
                    if staging.stat().st_mtime < cutoff:
                        remove_owned(staging, self.managed_roots)
                except (OSError, DeleteRefused) as e:
                    log.info("left staging folder %s: %s", staging, e)

    # -- internals -----------------------------------------------------------

    def _lock(self, tool_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(tool_id, threading.Lock())

    def _kept_versions(self, inst: InstalledTool) -> set[str]:
        return {str(inst.current)} | ({str(inst.previous)} if inst.previous else set())

    def _claim(self, tool_dir: Path) -> None:
        """Take ownership of tool_dir, refusing a pre-existing folder we did not create."""
        if tool_dir.exists() and not is_owned(tool_dir) and any(tool_dir.iterdir()):
            raise InstallError(
                _("Folder {path} already exists and was not created by ToolManager. "
                  "Move it away and try again.", path=tool_dir)
            )
        mark_owned(tool_dir)

    def _to_trash(self, path: Path, *, label: str, reason: str, tool_id: str) -> None:
        try:
            self.trash.move(path, label=label, reason=reason, tool_id=tool_id)
        except DeleteRefused as e:
            log.error("delete refused: %s", e)
            raise InstallError(_("Refused to remove {path}: {error}", path=path, error=e)) from e
        except OSError:
            raise InstallError(_("Files are in use. Close the tool and try again.")) from None

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
        if staging.exists():
            try:
                remove_owned(staging, self.managed_roots)
            except (OSError, DeleteRefused) as e:
                raise InstallError(_("Another installation is in progress")) from e
        mark_owned(staging)
        try:
            # Antivirus often holds the fresh zip open; a leftover temp folder must not
            # turn a successful install into a failure.
            with tempfile.TemporaryDirectory(dir=self.downloads, ignore_cleanup_errors=True) as tmp:
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
                    raise InstallError(str(e)) from e
            flatten_single_root(staging, ignore=MARKERS)
            if not (staging / release.entry).is_file():
                raise InstallError(
                    _("Package does not contain the entry file '{entry}'", entry=release.entry)
                )
            write_files_manifest(staging)
            (staging / COMPLETE_MARKER).touch()
            return staging
        except BaseException:
            try:
                remove_owned(staging, self.managed_roots)
            except (OSError, DeleteRefused) as e:
                log.warning("could not remove staging %s: %s", staging, e)
            raise

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
        if inst.shortcut is not None:
            data["shortcut"] = str(inst.shortcut)
        path = self.root / inst.id / STATE_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(tomli_w.dumps(data).encode("utf-8"))
        os.replace(tmp, path)

    def _prune(self, inst: InstalledTool) -> None:
        """Move unneeded old versions to the Trash; keep any that hold user files."""
        keep = self._kept_versions(inst)
        for child in (self.root / inst.id).iterdir():
            if not child.is_dir() or child.name.startswith(".") or child.name in keep:
                continue
            changed = user_files(child)
            if changed:
                log.info("keeping %s: %d user file(s)", child, len(changed))
                continue
            try:
                self.trash.move(child, label=f"{inst.name} {child.name}",
                                reason="old version", tool_id=inst.id)
            except (OSError, DeleteRefused) as e:
                # Typically an old version still running; retried after the next install.
                log.info("could not move %s to trash yet: %s", child, e)

    def _refresh_shortcut(self, inst: InstalledTool) -> InstalledTool:
        if inst.type is not ToolType.EXE:
            return inst
        lnk = self.shortcuts.create(inst.name, self.entry_path(inst), inst.id, inst.shortcut)
        return replace(inst, shortcut=lnk or inst.shortcut)
