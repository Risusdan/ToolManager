"""Shared UI state, configuration, catalog sync and the install/update/remove actions."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from nicegui import app, ui
from nicegui import run as ng_run
from packaging.version import Version

from .. import __version__
from ..backends import create_publisher, create_repository
from ..core import diagnostics, paths
from ..core.catalog_cache import CatalogCache
from ..core.credentials import CredentialError
from ..core.installer import Installer, InstallError, ToolStatus, UpdateState
from ..core.manifest import ManifestError
from ..core.models import Catalog, Release, Tool
from ..core.notices import Update, UpdateNotifier
from ..core.prefs import Prefs
from ..core.publish import Publisher
from ..core.python_env import EnvManager, find_runtime, make_backend
from ..core.report import known_issues_url, new_issue_url
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
    prefs: Prefs | None = None  # favorites and recently used tools
    # Python script tools: None when no interpreter is available
    env_manager: EnvManager | None = None
    runtime_problem: str = ""
    # tool id -> last run of that tool (kept while the app runs, so the log survives navigation)
    runs: dict[str, object] = field(default_factory=dict)
    # tool id -> last form values
    run_values: dict[str, dict] = field(default_factory=dict)
    # tool id -> running operation
    busy: dict[str, Operation] = field(default_factory=dict)
    publisher: Publisher | None = None  # write access for the publish wizard
    # Whether this account may write to the catalog repo (None: not asked yet). Only then
    # the publish wizard is shown: the tool developers have write access, users do not.
    can_write: bool | None = None
    # The single-instance lock; released right before restarting into an update.
    instance: object | None = None
    notifier: UpdateNotifier = field(default_factory=UpdateNotifier)
    self_update_announced: bool = False
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
    if state.prefs is None:
        state.prefs = Prefs(paths.data_root() / "prefs.json")
    state.settings = settings
    state.retention_days = settings.trash_retention_days
    state.installer = None
    state.cache = None
    state.publisher = None
    state.can_write = None
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
    try:
        state.publisher = create_publisher(settings, repo)
    except RepositoryError as e:
        log.info("publishing not available: %s", e)
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
    _configure_python(settings)
    # Show the cached catalog immediately; sync refreshes it in the background.
    try:
        cached = cache.load_cached()
        if cached:
            _apply(cached)
    except ManifestError as e:
        log.warning("cached catalog unreadable: %s", e)
    return None


def _configure_python(settings: Settings) -> None:
    state.env_manager = None
    runtime = find_runtime(settings.python_exe, settings.uv_exe)
    if runtime is None:
        state.runtime_problem = _("The Python interpreter shipped with ToolManager was not found.")
        log.warning("no Python runtime found")
        return
    backend = make_backend(settings.env_backend, runtime, settings.pypi_index_url,
                           paths.data_root() / "cache" / "uv")
    state.env_manager = EnvManager(paths.envs_dir(), backend)
    state.runtime_problem = ""
    # Environments of versions that are no longer installed are rebuilt on demand anyway.
    keep = {(inst.id, version) for inst in state.installer.list_installed()
            for version in inst.entries}
    state.env_manager.prune(keep)


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
    # Let the new instance become "the" instance; otherwise it would see this one as still
    # running, ask it to come to the front and quit, leaving nothing open.
    if state.instance is not None:
        state.instance.release()
    try:
        state.self_updater.restart()
    except SelfUpdateError as e:
        if state.instance is not None:
            state.instance.reclaim()  # still running: stay the single instance
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
    await check_write_access()
    await _auto_update()
    await _self_update()
    _notify_updates()


async def check_write_access() -> None:
    if state.publisher is None or state.can_write is not None:
        return
    try:
        state.can_write = await ng_run.io_bound(state.publisher.can_write)
    except RepositoryError as e:
        log.info("write access unknown: %s", e)  # asked again at the next sync
        return
    log.info("catalog write access: %s", state.can_write)
    changed()


def _notify_updates() -> None:
    """Tray notifications for new versions (each version once) and a ready self-update."""
    from .tray import tray

    if not tray.available:
        return
    updates = [Update(t.id, t.name, str(st.latest.version), st.important)
               for t, st in tools_with_updates()]
    message = state.notifier.message(updates)
    if message:
        tray.notify(*message)
    if state.self_status == "ready" and not state.self_update_announced:
        state.self_update_announced = True
        tray.notify(_("ToolManager {version} is ready", version=state.self_version),
                    _("It is used the next time ToolManager starts."))


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
        return
    record_use(tool_id)


def record_use(tool_id: str) -> None:
    if state.prefs is not None:
        state.prefs.record_use(tool_id)
        changed()


def toggle_favorite(tool_id: str) -> None:
    if state.prefs is not None:
        state.prefs.toggle_favorite(tool_id)
        changed()


def set_auto_update(tool_id: str, enabled: bool) -> None:
    state.installer.set_auto_update(tool_id, enabled)
    changed()


def _recent_output(tool_id: str) -> list[str]:
    """Output of the tool's latest run: the one in memory, else the newest saved run log."""
    session = state.runs.get(tool_id)
    if session is not None and getattr(session, "lines", None):
        return list(session.lines)
    run_logs = sorted((paths.logs_dir() / "runs" / tool_id).glob("*.log"))
    if not run_logs:
        return []
    try:
        return run_logs[-1].read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _open_url(url: str) -> None:
    log.info("opening %s", url)
    try:
        webbrowser.open(url)
    except OSError as e:
        ui.notify(_("Could not open the browser: {error}", error=e), type="negative")


def open_known_issues(tool: Tool) -> None:
    if state.catalog and state.catalog.jira:
        _open_url(known_issues_url(state.catalog.jira, tool))


def open_new_issue(tool: Tool) -> None:
    if not (state.catalog and state.catalog.jira):
        return
    inst = state.installer.installed(tool.id) if state.installer else None
    _open_url(new_issue_url(state.catalog.jira, tool, str(inst.current) if inst else None,
                            __version__, _recent_output(tool.id)))


def _diagnostics_summary() -> dict[str, str]:
    import platform

    settings = state.settings
    bb = settings.bitbucket
    summary = {
        "ToolManager": __version__,
        "Python": sys.version.split()[0],
        "OS": platform.platform(),
        "Data folder": str(paths.data_root()),
        "Launcher": os.environ.get("TOOLMANAGER_LAUNCHER", "(none: development run)"),
        "Catalog": (settings.local_catalog if settings.backend == "local"
                    else f"{bb.base_url} {bb.project}/{bb.repo} ({bb.branch})"),
        "Last sync": (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state.synced_at))
                      if state.synced_at else "never"),
        "Sync status": state.sync_status or "-",
        "Self update": f"{state.self_status or '-'} {state.self_version} {state.self_error}",
        "Python tools": f"{settings.env_backend}; index {settings.pypi_index_url or '-'}",
    }
    installed = state.installer.list_installed() if state.installer else []
    for inst in installed:
        previous = f" (previous {inst.previous})" if inst.previous else ""
        summary[f"Installed {inst.id}"] = f"{inst.current}{previous}"
    return summary


async def export_diagnostics() -> None:
    """Zip logs and settings for a problem report and show the file in Explorer."""
    launcher = os.environ.get("TOOLMANAGER_LAUNCHER")
    log_dirs = [paths.logs_dir()]
    if launcher:  # launcher.log sits next to the launcher (not in data\ for the customer edition)
        log_dirs.append(Path(launcher).parent / "logs")
    try:
        target = await ng_run.io_bound(
            diagnostics.build, paths.data_root() / "diagnostics", _diagnostics_summary(),
            {"settings.toml": paths.settings_file(), "defaults.toml": paths.defaults_file()},
            log_dirs)
    except OSError as e:
        ui.notify(_("Could not export diagnostics: {error}", error=e), type="negative")
        return
    log.info("diagnostics exported to %s", target)
    ui.notify(_("Saved {name}. Attach it to your problem report.", name=target.name),
              type="positive", timeout=8000)
    reveal_file(target)


def reveal_file(path: Path) -> None:
    """Open the folder with the file selected (Windows); elsewhere just the folder."""
    if sys.platform == "win32":
        # A string, not a list: explorer wants /select,"C:\\path with spaces", which the
        # list form would quote as a whole.
        subprocess.Popen(f'explorer /select,"{path}"')
    else:
        open_folder(path.parent)


def can_publish(tool: Tool | None = None) -> bool:
    """The publish wizard is for developers: accounts with write access to the catalog repo.
    New versions of a tool: its owners, and admins (docs/DESIGN.md §8.2)."""
    if not (state.publisher and state.can_write):
        return False
    if tool is None:
        return True
    user = state.publisher.known_user()
    admins = state.catalog.admins if state.catalog else ()
    return bool(user) and (user in tool.owners or user in admins)


def open_folder(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as e:
        ui.notify(_("Could not open folder: {error}", error=e), type="negative")
