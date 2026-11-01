"""ToolManager updating itself through the launcher layout (see docs/DESIGN.md §6.2).

ToolManager is published in the catalog like any tool, with the id "toolmanager"; its
release zip holds the onedir build (ToolManagerApp.exe and its files). Updating means
preparing a new complete app-x.y.z folder next to the launcher; the launcher starts it on
the next launch. Nothing running is ever overwritten.

Only active when started by the launcher (TOOLMANAGER_LAUNCHER is set). In development the
app runs from source and never updates itself.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from packaging.version import Version

from ..i18n import _
from .archive import UnsafeArchiveError, flatten_single_root, safe_extract
from .models import Catalog, Release, Tool
from .repository import Progress, Repository, RepositoryError
from .trash import (
    COMPLETE_MARKER,
    OWNER_MARKER,
    DeleteRefused,
    Trash,
    mark_owned,
    remove_owned,
)

log = logging.getLogger(__name__)

SELF_TOOL_ID = "toolmanager"
APP_EXE = "ToolManagerApp.exe"
DISABLED_MARKER = ".disabled"
STAGING_PREFIX = ".staging-app-"
_APP_DIR = re.compile(r"^app-(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class SelfUpdateError(Exception):
    """User-facing failure; the message is already translated."""


@dataclass(frozen=True)
class SelfInstall:
    root: Path  # folder holding ToolManager.exe (the launcher) and the app-x.y.z folders
    launcher: Path
    current: Version
    failed_version: str | None = None  # a newer version the launcher could not start


@dataclass(frozen=True)
class AppVersion:
    version: Version
    path: Path
    complete: bool
    disabled: bool


def detect(current: Version, env: Mapping[str, str] = os.environ) -> SelfInstall | None:
    launcher = env.get("TOOLMANAGER_LAUNCHER")
    if not launcher:
        return None
    path = Path(launcher)
    return SelfInstall(
        root=path.parent,
        launcher=path,
        current=current,
        failed_version=env.get("TOOLMANAGER_FAILED_VERSION") or None,
    )


class SelfUpdater:
    def __init__(
        self,
        install: SelfInstall,
        repo: Repository,
        revision: Callable[[], str | None],
        trash: Trash,
    ) -> None:
        self.install = install
        self.repo = repo
        self.revision = revision
        self.trash = trash

    # -- queries ---------------------------------------------------------------

    def versions_on_disk(self) -> list[AppVersion]:
        result = []
        try:
            entries = list(self.install.root.iterdir())
        except OSError:
            return []
        for path in entries:
            m = _APP_DIR.match(path.name)
            if not m or not path.is_dir() or path.is_symlink():
                continue
            result.append(AppVersion(
                version=Version(".".join(m.groups())),
                path=path,
                complete=(path / COMPLETE_MARKER).is_file(),
                disabled=(path / DISABLED_MARKER).exists(),
            ))
        return sorted(result, key=lambda v: v.version, reverse=True)

    def pending(self) -> Version | None:
        """A newer, complete version that the launcher will start next time.

        A version the launcher just reported as failing is not offered: restarting into it
        would fail again.
        """
        failed = self.install.failed_version
        for v in self.versions_on_disk():
            if (v.version > self.install.current and v.complete and not v.disabled
                    and str(v.version) != failed):
                return v.version
        return None

    def available(self, tool: Tool | None) -> Release | None:
        """The catalog release to download, or None if there is nothing (new) to do."""
        if tool is None or tool.latest is None or tool.latest.version <= self.install.current:
            return None
        on_disk = {v.version: v for v in self.versions_on_disk()}
        existing = on_disk.get(tool.latest.version)
        if existing is not None and (existing.complete or existing.disabled):
            return None  # already prepared, or the user disabled this version
        return tool.latest

    @staticmethod
    def catalog_requires_update(catalog: Catalog, current: Version) -> bool:
        return catalog.min_client_version > current

    # -- operations ----------------------------------------------------------------

    def download(self, release: Release, progress: Progress | None = None) -> Path:
        """Prepare app-<version>; the launcher picks it up on the next start."""
        root = self.install.root
        target = root / f"app-{release.version}"
        if (target / COMPLETE_MARKER).is_file():
            return target
        if target.exists():
            raise SelfUpdateError(
                _("Folder {path} already exists and was not created by ToolManager. "
                  "Move it away and try again.", path=target)
            )
        revision = self.revision()
        if revision is None:
            raise SelfUpdateError(_("Catalog has not been synced yet"))

        staging = root / f"{STAGING_PREFIX}{release.version}"
        if staging.exists():
            try:
                remove_owned(staging, [root])
            except (OSError, DeleteRefused) as e:
                raise SelfUpdateError(_("Another installation is in progress")) from e
        mark_owned(staging)
        try:
            with tempfile.TemporaryDirectory(dir=root, prefix=".download-",
                                             ignore_cleanup_errors=True) as tmp:
                archive = Path(tmp) / "toolmanager.zip"
                try:
                    self.repo.download_artifact(revision, release.artifact, archive, progress)
                except RepositoryError as e:
                    raise SelfUpdateError(_("Download failed: {error}", error=e)) from e
                if not zipfile.is_zipfile(archive):
                    raise SelfUpdateError(_("Downloaded file is not a valid zip"))
                try:
                    safe_extract(archive, staging)
                except UnsafeArchiveError as e:
                    raise SelfUpdateError(str(e)) from e
            flatten_single_root(staging, ignore=frozenset({OWNER_MARKER}))
            if not (staging / APP_EXE).is_file():
                raise SelfUpdateError(
                    _("Package does not contain the entry file '{entry}'", entry=APP_EXE)
                )
            (staging / COMPLETE_MARKER).touch()  # last: the launcher may use it from now on
            staging.rename(target)
            log.info("prepared ToolManager %s in %s", release.version, target)
            return target
        except BaseException:
            try:
                remove_owned(staging, [root])
            except (OSError, DeleteRefused) as e:
                log.warning("could not remove %s: %s", staging, e)
            raise

    def disable(self, version: str) -> None:
        """User-confirmed: stop the launcher from starting this version (nothing is deleted)."""
        path = self.install.root / f"app-{version}"
        if not _APP_DIR.match(path.name) or not path.is_dir():
            raise SelfUpdateError(_("Version {version} not found", version=version))
        if Version(version) == self.install.current:
            raise SelfUpdateError(_("This version is in use and cannot be removed"))
        (path / DISABLED_MARKER).touch()
        log.info("disabled ToolManager %s", version)

    def prune(self) -> None:
        """Move old app versions to the Trash, keeping the current one and one fallback."""
        if self.install.failed_version:
            return  # running as a fallback: keep everything for diagnosis
        older = [v for v in self.versions_on_disk()
                 if v.version < self.install.current and v.complete and not v.disabled]
        for v in older[1:]:  # older[0] stays as the launcher's fallback
            try:
                self.trash.move(v.path, label=f"ToolManager {v.version}", reason="old version",
                                tool_id=SELF_TOOL_ID)
            except (OSError, DeleteRefused) as e:
                log.info("could not move %s to trash: %s", v.path, e)

    def restart(self) -> None:
        """Start a new instance through the launcher; the caller then shuts this one down."""
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen([str(self.install.launcher)], cwd=self.install.root,
                             creationflags=flags, close_fds=True)
        except OSError as e:
            raise SelfUpdateError(_("Could not start the tool: {error}", error=e)) from e
