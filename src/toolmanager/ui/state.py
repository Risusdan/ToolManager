"""Shared UI state, configuration, catalog sync and the install/update/remove actions."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from nicegui import app, ui
from nicegui import run as ng_run
from packaging.version import Version

from .. import __version__
from ..backends import create_repository
from ..core import paths
from ..core.catalog_cache import CatalogCache
from ..core.credentials import CredentialError
from ..core.installer import Installer, InstallError, ToolStatus, UpdateState
from ..core.manifest import ManifestError
from ..core.models import Catalog, Release, Tool
from ..core.repository import RepositoryError
from ..core.self_update import SELF_TOOL_ID, SelfUpdateError, SelfUpdater, detect
from ..core.settings import Settings
from ..core.trash import Trash
from ..i18n import _

log = logging.getLogger(__name__)


@dataclass
class Operation:
    """A running install/update, polled by the UI (progress is written from a worker thread)."""

    label: str
    value: float = 0.0


@dataclass
class AppState:
    catalog: Catalog | None = None
    problems: tuple[ManifestError, ...] = ()
    installer: Installer | None = None
    cache: CatalogCache | None = None
    settings: Settings = field(default_factory=Settings)
    # True when --catalog points at a local folder: saved settings are not applied.
    dev_override: bool = False
    retention_days: int = 7
    # "" (idle), "syncing" or "failed"; only the latter two are shown prominently.
    sync_status: str = ""
    sync_error: str = ""
    synced_at: float | None = None
    # ToolManager updating itself (only when started by the launcher)
    self_updater: SelfUpdater | None = None
    self_tool: Tool | None = None  # ToolManager's own catalog entry, hidden from the tool list
    self_status: str = ""  # "" | "downloading" | "ready" | "failed"
    self_version: str = ""  # version being downloaded / ready
    self_progress: Operation = field(default_factory=lambda: Operation(""))
    self_error: str = ""
    failed_version: str | None = None  # newer version the launcher could not start
    catalog_needs_newer_app: bool = False
    # tool id -> running operation
    busy: dict[str, Operation] = field(default_factory=dict)
    # Bumped whenever the catalog or installed tools change so open pages re-render.
    generation: int = 0


state = AppState()


def changed() -> None:
    state.generation += 1


def watch(*renders) -> None:
    """Re-render the given refreshables whenever state changes."""
    seen = state.generation

    def check() -> None:
        nonlocal seen
        if seen != state.generation:
            seen = state.generation
            for render in renders:
                render.refresh()

    ui.timer(0.5, check)


def status_of(tool: Tool) -> ToolStatus | None:
    return state.installer.status(tool) if state.installer else None


def tools_with_updates() -> list[tuple[Tool, ToolStatus]]:
    if not (state.catalog and state.installer):
        return []
    result = []
    for tool in state.catalog.tools:
        st = state.installer.status(tool)
        if st.state is UpdateState.UPDATE_AVAILABLE:
            result.append((tool, st))
    return result


# -- configuration and sync -------------------------------------------------------


def _apply(result) -> None:
    catalog = result.catalog
    # ToolManager's own entry drives self-update; it is not a tool users install.
    state.self_tool = catalog.tool(SELF_TOOL_ID)
    state.catalog = replace(catalog, tools=tuple(t for t in catalog.tools if t.id != SELF_TOOL_ID))
    state.catalog_needs_newer_app = catalog.min_client_version > Version(__version__)
    state.problems = result.problems
    changed()
    for p in result.problems:
        log.warning("manifest problem: %s", p)


def configure(settings: Settings) -> str | None:
    """(Re)build repository, cache and installer from settings. Returns an error message."""
    state.settings = settings
    state.retention_days = settings.trash_retention_days
    state.installer = None
    state.cache = None
    state.catalog = None
    state.problems = ()
    state.sync_status = ""
    changed()
    if not settings.server_configured():
        return _("Catalog not configured")
    try:
        repo = create_repository(settings)
    except (RepositoryError, CredentialError) as e:
        log.warning("repository not available: %s", e)
        return str(e)
    cache = CatalogCache(repo, paths.cache_dir())
    managed = [paths.tools_dir(), paths.downloads_dir()]
    state.installer = Installer(
        repo,
        root=managed[0],
        downloads=managed[1],
        revision=lambda: cache.revision,
        client_version=Version(__version__),
        trash=Trash(paths.trash_dir(), managed, settings.trash_retention_days),
    )
    state.installer.cleanup()
    state.cache = cache
    _configure_self_update(repo, cache, settings)
    # Show the cached catalog immediately; sync refreshes it in the background.
    try:
        cached = cache.load_cached()
        if cached:
            _apply(cached)
    except ManifestError as e:
        log.warning("cached catalog unreadable: %s", e)
    return None


def _configure_self_update(repo, cache: CatalogCache, settings: Settings) -> None:
    state.self_updater = None
    install = detect(Version(__version__))
    if install is None:
        return  # running from source: no self-update
    state.failed_version = install.failed_version
    trash = Trash(paths.trash_dir(), [install.root], settings.trash_retention_days)
    state.self_updater = SelfUpdater(install, repo, lambda: cache.revision, trash)
    state.self_updater.prune()
    pending = state.self_updater.pending()
    if pending:
        state.self_status, state.self_version = "ready", str(pending)


async def _self_update() -> None:
    """Download a new ToolManager in the background; it takes effect on the next start."""
    updater = state.self_updater
    if updater is None or state.self_status in ("downloading", "ready"):
        return
    release = updater.available(state.self_tool)
    if release is None:
        return
    state.self_status, state.self_version = "downloading", str(release.version)
    state.self_error = ""
    state.self_progress.value = 0.0
    changed()
    try:
        await ng_run.io_bound(updater.download, release, _progress_setter(state.self_progress))
        state.self_status = "ready"
    except (SelfUpdateError, OSError) as e:
        log.warning("self-update download failed: %s", e)
        state.self_status, state.self_error = "failed", str(e)
    changed()


def restart_to_update() -> None:
    try:
        state.self_updater.restart()
    except SelfUpdateError as e:
        ui.notify(str(e), type="negative")
        return
    ui.notify(_("Restarting ToolManager..."))
    ui.timer(0.5, app.shutdown, once=True)


def disable_failed_version() -> None:
    version = state.failed_version
    try:
        state.self_updater.disable(version)
    except SelfUpdateError as e:
        ui.notify(str(e), type="negative")
        return
    state.failed_version = None
    ui.notify(_("ToolManager {version} will no longer be started", version=version))
    changed()


async def sync() -> None:
    cache = state.cache
    if cache is None or state.sync_status == "syncing":
        return
    state.sync_status = "syncing"
    changed()
    try:
        result, _changed_remote = await ng_run.io_bound(cache.sync)
    except (RepositoryError, ManifestError, OSError) as e:
        log.exception("catalog sync failed")
        state.sync_status = "failed"
        state.sync_error = str(e)
        changed()
        return
    state.sync_status = ""
    state.synced_at = time.time()
    _apply(result)
    await _auto_update()
    await _self_update()


async def _auto_update() -> None:
    if not (state.installer and state.catalog):
        return
    for tool in state.installer.auto_update_candidates(state.catalog.tools):
        try:
            await ng_run.io_bound(state.installer.install, tool)
            log.info("auto-updated %s", tool.id)
        except InstallError as e:
            log.warning("auto-update of %s failed: %s", tool.id, e)
    changed()


# -- actions ------------------------------------------------------------------------


async def _run_op(tool_id: str, label: str, fn: Callable[[Operation], object]) -> bool:
    """Run a blocking installer call off the event loop with progress and notifications."""
    if tool_id in state.busy:
        return False
    op = Operation(label=label)
    state.busy[tool_id] = op
    changed()
    try:
        await ng_run.io_bound(fn, op)
        return True
    except InstallError as e:
        ui.notify(str(e), type="negative", multi_line=True)
        return False
    except Exception as e:  # unexpected: keep the UI alive, log the details
        log.exception("operation failed: %s %s", label, tool_id)
        ui.notify(_("Unexpected error: {error}", error=e), type="negative")
        return False
    finally:
        state.busy.pop(tool_id, None)
        changed()


def _progress_setter(op: Operation):
    def progress(done: int, total: int | None) -> None:
        op.value = done / total if total else 0.0

    return progress


async def install(tool: Tool, release: Release | None = None) -> None:
    target = release or tool.latest
    ok = await _run_op(
        tool.id,
        _("Installing {version}...", version=target.version if target else ""),
        lambda op: state.installer.install(tool, release, _progress_setter(op)),
    )
    if ok:
        ui.notify(_("{name} {version} installed", name=tool.name, version=target.version),
                  type="positive")


async def update_all() -> None:
    for tool, _st in tools_with_updates():
        await install(tool)


async def open_installer(tool: Tool) -> None:
    await _run_op(
        tool.id,
        _("Downloading installer..."),
        lambda op: state.installer.open_installer(tool, None, _progress_setter(op)),
    )


async def switch_to_previous(tool: Tool) -> None:
    if await _run_op(tool.id, _("Switching version..."),
                     lambda _op: state.installer.rollback(tool.id)):
        inst = state.installer.installed(tool.id)
        ui.notify(_("Now using {version}", version=inst.current), type="positive")


async def confirm(title: str, body: list[str], action: str, warning: str = "") -> bool:
    """Confirmation dialog; the action button is the only red element in the UI."""
    with ui.dialog() as dialog, ui.card().classes("w-[440px] gap-3 p-5"):
        ui.label(title).classes("text-base font-semibold")
        for line in body:
            ui.label(line).classes("text-sm text-slate-600")
        if warning:
            with ui.row().classes(
                "w-full no-wrap items-start gap-2 bg-amber-50 text-amber-900 rounded px-3 py-2"
            ):
                ui.icon("info", size="18px").classes("mt-0.5")
                ui.label(warning).classes("text-sm")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button(_("Cancel"), on_click=lambda: dialog.submit(False)).props(
                "flat color=grey-8"
            )
            ui.button(action, color="red", on_click=lambda: dialog.submit(True)).props(
                "unelevated"
            )
    return bool(await dialog)


async def uninstall(tool_id: str, name: str) -> None:
    files = await ng_run.io_bound(state.installer.user_files_of, tool_id)
    warning = (
        _("It contains {count} file(s) you created or changed, e.g. {example}. They go to the "
          "Trash with the tool.", count=len(files), example=files[0])
        if files else ""
    )
    body = [_("The tool folder is moved to the Trash and can be restored for {days} days.",
              days=state.retention_days)]
    if await confirm(_("Uninstall {name}?", name=name), body, _("Uninstall"), warning):
        if await _run_op(tool_id, _("Uninstalling..."),
                         lambda _op: state.installer.uninstall(tool_id)):
            ui.notify(_("{name} uninstalled", name=name))


async def discard_version(tool_id: str, version: str, name: str) -> None:
    body = [_("The folder is moved to the Trash and can be restored for {days} days.",
              days=state.retention_days)]
    if await confirm(_("Move {name} {version} to the Trash?", name=name, version=version),
                     body, _("Move to Trash")):
        await _run_op(tool_id, _("Moving to Trash..."),
                      lambda _op: state.installer.discard_version(tool_id, version))


def launch(tool_id: str) -> None:
    try:
        state.installer.launch(tool_id)
    except InstallError as e:
        ui.notify(str(e), type="negative")


def set_auto_update(tool_id: str, enabled: bool) -> None:
    state.installer.set_auto_update(tool_id, enabled)
    changed()


def open_folder(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as e:
        ui.notify(_("Could not open folder: {error}", error=e), type="negative")
