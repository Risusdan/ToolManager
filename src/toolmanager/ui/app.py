"""NiceGUI UI: browse/search, tool detail with install/update/rollback, installed tools."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from nicegui import app, native, ui
from nicegui import run as ng_run
from packaging.version import Version

from .. import __version__
from ..backends import create_repository
from ..core import paths
from ..core.catalog_cache import CatalogCache
from ..core.credentials import CredentialError
from ..core.installer import Installer, InstallError, ToolStatus, UpdateState
from ..core.logging_setup import setup_logging
from ..core.manifest import ManifestError
from ..core.models import Catalog, Release, Tool, ToolType
from ..core.readme import rewrite_relative_images
from ..core.repository import RepositoryError
from ..core.settings import Settings
from ..core.trash import Trash, format_time
from ..i18n import _, set_language

log = logging.getLogger(__name__)

CATALOG_URL = "/catalog"


@dataclass
class Operation:
    """A running install/update, polled by the UI (progress is written from a worker thread)."""

    label: str
    value: float = 0.0


@dataclass
class AppState:
    catalog: Catalog | None = None
    problems: tuple[ManifestError, ...] = ()
    status: str = ""
    installer: Installer | None = None
    cache: CatalogCache | None = None
    settings: Settings = field(default_factory=Settings)
    # True when --catalog points at a local folder: saved settings are not applied.
    dev_override: bool = False
    retention_days: int = 7
    # tool id -> running operation
    busy: dict[str, Operation] = field(default_factory=dict)
    # Bumped whenever the catalog or installed tools change so open pages re-render.
    generation: int = 0


state = AppState()


def _changed() -> None:
    state.generation += 1


def _apply(result) -> None:
    state.catalog = result.catalog
    state.problems = result.problems
    _changed()
    for p in result.problems:
        log.warning("manifest problem: %s", p)


def _status(tool: Tool) -> ToolStatus | None:
    return state.installer.status(tool) if state.installer else None


def _matches(tool: Tool, query: str, category: str | None) -> bool:
    if category and tool.category != category:
        return False
    if not query:
        return True
    q = query.lower()
    return q in tool.name.lower() or q in tool.summary.lower() or q in tool.readme.lower()


def _watch_generation(*renders) -> None:
    seen = state.generation

    def check() -> None:
        nonlocal seen
        if seen != state.generation:
            seen = state.generation
            for render in renders:
                render.refresh()

    ui.timer(1.0, check)


def _updates_count() -> int:
    if not (state.catalog and state.installer):
        return 0
    return sum(
        1 for t in state.catalog.tools
        if state.installer.status(t).state is UpdateState.UPDATE_AVAILABLE
    )


def _header() -> None:
    ui.colors(primary="#2563eb")  # one accent colour for actions and "update available"
    with ui.header().classes("items-center justify-between bg-slate-800"):
        with ui.row().classes("items-center gap-6"):
            ui.link("ToolManager", "/").classes("text-xl font-bold text-white no-underline")
            ui.link(_("All tools"), "/").classes("text-slate-200 no-underline")

            @ui.refreshable
            def installed_link() -> None:
                with ui.link(target="/installed").classes(
                    "flex items-center gap-1 text-slate-200 no-underline"
                ):
                    ui.label(_("Installed"))
                    count = _updates_count()
                    if count:
                        ui.badge(str(count), color="primary").props("rounded")

            installed_link()
            _watch_generation(installed_link)
            ui.link(_("Trash"), "/trash").classes("text-slate-200 no-underline")
        with ui.row().classes("items-center gap-3"):
            ui.label().bind_text_from(state, "status").classes("text-sm text-slate-300")
            ui.button(icon="settings", on_click=lambda: ui.navigate.to("/settings")).props(
                "flat round dense color=white"
            ).tooltip(_("Settings"))


# -- operations ----------------------------------------------------------------


async def _run_op(tool_id: str, label: str, fn: Callable[[Operation], object]) -> bool:
    """Run a blocking installer call off the event loop with progress and notifications."""
    if tool_id in state.busy:
        return False
    op = Operation(label=label)
    state.busy[tool_id] = op
    _changed()
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
        _changed()


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


async def open_installer(tool: Tool) -> None:
    await _run_op(
        tool.id,
        _("Downloading installer..."),
        lambda op: state.installer.open_installer(tool, None, _progress_setter(op)),
    )


async def rollback(tool: Tool) -> None:
    if await _run_op(tool.id, _("Rolling back..."), lambda _op: state.installer.rollback(tool.id)):
        inst = state.installer.installed(tool.id)
        ui.notify(_("Rolled back to {version}", version=inst.current), type="positive")


async def _confirm(title: str, body: list[str], action: str) -> bool:
    with ui.dialog() as dialog, ui.card().classes("max-w-lg"):
        ui.label(title).classes("text-lg font-semibold")
        for line in body:
            ui.label(line).classes("text-sm text-slate-600")
        with ui.row().classes("w-full justify-end"):
            ui.button(_("Cancel"), on_click=lambda: dialog.submit(False)).props("flat")
            ui.button(action, color="red", on_click=lambda: dialog.submit(True))
    return bool(await dialog)


def open_folder(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as e:
        ui.notify(_("Could not open folder: {error}", error=e), type="negative")


async def uninstall(tool_id: str, name: str) -> None:
    body = [_("The tool folder is moved to the Trash and can be restored for {days} days.",
              days=state.retention_days)]
    files = await ng_run.io_bound(state.installer.user_files_of, tool_id)
    if files:
        body.append(_("It contains {count} file(s) you created or changed, e.g. {example}.",
                      count=len(files), example=files[0]))
    if await _confirm(_("Uninstall {name}?", name=name), body, _("Uninstall")):
        if await _run_op(tool_id, _("Uninstalling..."),
                         lambda _op: state.installer.uninstall(tool_id)):
            ui.notify(_("{name} uninstalled", name=name))


async def discard_version(tool_id: str, version: str, name: str) -> None:
    body = [_("The folder is moved to the Trash and can be restored for {days} days.",
              days=state.retention_days)]
    if await _confirm(_("Move {name} {version} to the Trash?", name=name, version=version),
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
    _changed()


async def update_all() -> None:
    for tool in state.catalog.tools:
        if state.installer.status(tool).state is UpdateState.UPDATE_AVAILABLE:
            await install(tool)


# -- shared widgets --------------------------------------------------------------


def _recommended_hint(text: str) -> None:
    """Amber "update soon" marker; the tooltip explains what the flag means."""
    with ui.row().classes("items-center gap-1 text-amber-800 no-wrap") as row:
        ui.icon("warning_amber", size="16px")
        ui.label(text)
    row.tooltip(_("The author marked this update as important (e.g. a serious bug fix). "
                  "See the version notes for details."))


def _status_line(tool: Tool, show_reason: bool = True) -> None:
    """Quiet status: grey when all is well, colour only for what needs attention."""
    st = _status(tool)
    with ui.row().classes("items-center gap-2 text-sm text-slate-500"):
        if st is None or st.state is UpdateState.NOT_INSTALLED:
            latest = tool.latest
            ui.label(f"{_('Latest')}: {latest.version}" if latest else _("No releases"))
            return
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.icon("check", size="16px")
            ui.label(_("Installed {version}", version=st.installed.current))
        if st.state is UpdateState.UPDATE_AVAILABLE:
            ui.label("·")
            ui.label(_("Update to {version} available", version=st.latest.version)).classes(
                "text-primary"
            )
            if st.important:
                note = st.important_notes[0] if st.important_notes and show_reason else ""
                _recommended_hint(
                    _("Recommended update: {reason}", reason=note) if note
                    else _("Recommended update")
                )


def _busy_indicator(tool_id: str) -> bool:
    op = state.busy.get(tool_id)
    if op is None:
        return False
    with ui.column().classes("w-64 gap-1"):
        ui.label(op.label).classes("text-sm text-slate-600")
        ui.linear_progress(show_value=False).bind_value_from(op, "value")
    return True


def _action_buttons(tool: Tool, compact: bool = False) -> None:
    """Primary actions for a tool: install / update / open (and rollback etc. when not compact)."""
    if _busy_indicator(tool.id):
        return
    if tool.type is ToolType.INSTALLER:
        ui.button(_("Download and open installer"), icon="download",
                  on_click=lambda: open_installer(tool))
        return
    st = _status(tool)
    inst = st.installed if st else None
    if st is None:
        return
    if st.state is UpdateState.NOT_INSTALLED:
        if tool.latest:
            ui.button(_("Install {version}", version=tool.latest.version), icon="download",
                      on_click=lambda: install(tool))
        return
    if st.state is UpdateState.UPDATE_AVAILABLE:
        ui.button(_("Update to {version}", version=st.latest.version), icon="upgrade",
                  on_click=lambda: install(tool))
    run_btn = ui.button(_("Open"), icon="play_arrow", on_click=lambda: launch(tool.id)).props(
        "outline color=grey-8"
    )
    if tool.type is ToolType.PYTHON:
        run_btn.disable()
        run_btn.tooltip(_("Running Python tools is not supported yet"))
    if compact:
        return
    if inst.previous:
        ui.button(_("Roll back to {version}", version=inst.previous), icon="history",
                  on_click=lambda: rollback(tool)).props("flat color=grey-8")
    ui.button(_("Uninstall"), icon="delete", on_click=lambda: uninstall(tool.id, tool.name)
              ).props("flat color=grey-8")


# -- pages -----------------------------------------------------------------------


@ui.page("/")
def index_page() -> None:
    _header()
    filters = {"query": "", "category": None}

    @ui.refreshable
    def tool_grid() -> None:
        catalog = state.catalog
        if catalog is None:
            if state.cache is None:
                _setup_prompt()
            else:
                ui.label(state.status).classes("text-slate-500")
            return
        if state.problems:
            ui.label(_("{count} tool(s) skipped due to manifest errors", count=len(state.problems))
                     ).classes("text-amber-600 text-sm")
        tools = [t for t in catalog.tools if _matches(t, filters["query"], filters["category"])]
        if not tools:
            ui.label(_("No tools found")).classes("text-slate-500")
            return
        with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"):
            for tool in tools:
                _tool_card(tool, catalog)

    @ui.refreshable
    def category_list() -> None:
        catalog = state.catalog
        items = [(None, _("All tools"))]
        if catalog:
            items += [(c.id, c.name) for c in catalog.categories]
        for cat_id, name in items:
            active = filters["category"] == cat_id

            def select(cat_id=cat_id) -> None:
                filters["category"] = cat_id
                category_list.refresh()
                tool_grid.refresh()

            ui.button(name, on_click=select).props(
                f"flat align=left no-caps {'color=primary' if active else 'color=grey-8'}"
            ).classes("w-full")

    with ui.left_drawer(value=True).classes("bg-slate-50"):
        ui.label(_("Categories")).classes("text-xs uppercase text-slate-500 mb-2")
        category_list()

    def on_search(e) -> None:
        filters["query"] = e.value or ""
        tool_grid.refresh()

    ui.input(placeholder=_("Search tools..."), on_change=on_search).props(
        "outlined dense clearable"
    ).classes("w-full max-w-xl mb-4")
    tool_grid()
    _watch_generation(tool_grid, category_list)


def _tool_card(tool: Tool, catalog: Catalog) -> None:
    category = catalog.category(tool.category)
    with ui.card().classes("cursor-pointer hover:shadow-lg").on(
        "click", lambda t=tool: ui.navigate.to(f"/tool/{t.id}")
    ):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(tool.name).classes("text-lg font-semibold")
            ui.badge(category.name if category else tool.category, color="grey-7").props(
                "outline"
            )
        ui.label(tool.summary).classes("text-slate-600")
        _status_line(tool)


@ui.page("/tool/{tool_id}")
def tool_page(tool_id: str) -> None:
    _header()

    @ui.refreshable
    def detail() -> None:
        tool = state.catalog.tool(tool_id) if state.catalog else None
        ui.button(_("Back"), icon="arrow_back", on_click=ui.navigate.back).props("flat no-caps")
        if tool is None:
            ui.label(_("No tools found"))
            return
        ui.label(tool.name).classes("text-2xl font-bold")
        ui.label(tool.summary).classes("text-slate-600")
        ui.label(f"{_('Owners')}: {', '.join(tool.owners)}").classes("text-sm text-slate-500")
        _status_line(tool, show_reason=False)  # the note below carries the reason
        st = _status(tool)
        if st and st.important and st.state is UpdateState.UPDATE_AVAILABLE:
            with ui.row().classes(
                "items-center gap-2 bg-amber-50 text-amber-900 rounded px-3 py-2 text-sm"
            ):
                ui.icon("warning_amber", size="18px")
                if st.important_notes:
                    ui.label(_("The author recommends updating soon: {reason}",
                               reason="; ".join(st.important_notes)))
                else:
                    ui.label(_("The author recommends updating soon. The reason is in the notes "
                               "of the versions marked \"Recommended\" under Version history."))
        with ui.row().classes("items-center gap-2 my-2"):
            _action_buttons(tool)
        if st and st.installed:
            ui.switch(_("Update automatically"), value=st.installed.auto_update,
                      on_change=lambda e: set_auto_update(tool.id, e.value))
        with ui.row().classes("w-full gap-8 items-start no-wrap"):
            with ui.column().classes("flex-grow"):
                readme = rewrite_relative_images(tool.readme, f"{CATALOG_URL}/tools/{tool.id}")
                ui.markdown(readme).classes("w-full")
            with ui.column().classes("w-80 shrink-0"):
                ui.label(_("Version history")).classes("text-lg font-semibold")
                for rel in tool.releases:
                    _release_card(tool, rel, st)

    detail()
    _watch_generation(detail)


def _release_card(tool: Tool, rel: Release, st: ToolStatus | None) -> None:
    current = st.installed.current if st and st.installed else None
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(str(rel.version)).classes("font-semibold")
            ui.label(rel.date.isoformat()).classes("text-xs text-slate-500")
            if rel.important:
                _recommended_hint(_("Recommended"))
            if rel.version == current:
                ui.label("· " + _("Current")).classes("text-xs text-slate-500")
            elif tool.type is not ToolType.INSTALLER and st and tool.id not in state.busy:
                ui.space()
                ui.button(_("Install this version"), on_click=lambda r=rel: install(tool, r)
                          ).props("flat dense size=sm no-caps")
        if rel.important and rel.important_note:
            ui.label(rel.important_note).classes("text-sm text-amber-900")
        ui.markdown(rel.changelog).classes("text-sm")


@ui.page("/installed")
def installed_page() -> None:
    _header()

    @ui.refreshable
    def listing() -> None:
        installer = state.installer
        if installer is None:
            ui.label(_("Catalog not configured")).classes("text-slate-500")
            return
        installed = installer.list_installed()
        with ui.row().classes("w-full items-center justify-between mb-2"):
            ui.label(_("Installed tools")).classes("text-2xl font-bold")
            if _updates_count():
                ui.button(_("Update all"), icon="upgrade", on_click=update_all)
        if not installed:
            ui.label(_("No tools installed yet")).classes("text-slate-500")
            return
        for inst in installed:
            tool = state.catalog.tool(inst.id) if state.catalog else None
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center gap-4"):
                    with ui.column().classes("gap-0 flex-grow"):
                        with ui.row().classes("items-center gap-2"):
                            if tool:
                                ui.link(inst.name, f"/tool/{inst.id}").classes(
                                    "text-lg font-semibold no-underline text-slate-900"
                                )
                            else:
                                ui.label(inst.name).classes("text-lg font-semibold")
                                ui.label(_("Removed from catalog")).classes(
                                    "text-xs text-slate-500"
                                )
                        if tool:
                            _status_line(tool)
                        else:
                            ui.label(_("Installed {version}", version=inst.current)).classes(
                                "text-sm text-slate-500"
                            )
                    ui.switch(_("Update automatically"), value=inst.auto_update,
                              on_change=lambda e, i=inst.id: set_auto_update(i, e.value))
                    if tool:
                        _action_buttons(tool, compact=True)
                    else:
                        ui.button(_("Uninstall"), icon="delete",
                                  on_click=lambda i=inst: uninstall(i.id, i.name)).props(
                            "flat color=grey-8"
                        )
                for kept in installer.retained_versions(inst.id):
                    _retained_row(inst.name, kept)

    listing()
    _watch_generation(listing)


def _retained_row(name: str, kept) -> None:
    with ui.row().classes("w-full items-center gap-2 bg-amber-50 rounded p-2 no-wrap"):
        ui.icon("folder_special", color="amber-8")
        ui.label(
            _("Old version {version} was kept because it contains {count} file(s) you created "
              "or changed (e.g. {example}).", version=kept.version,
              count=len(kept.user_files), example=kept.user_files[0])
        ).classes("text-sm flex-grow")
        ui.button(_("Open folder"), on_click=lambda: open_folder(kept.path)).props(
            "flat dense no-caps"
        )
        ui.button(
            _("Move to Trash"),
            on_click=lambda: discard_version(kept.tool_id, kept.version, name),
        ).props("flat dense no-caps color=grey-8")


@ui.page("/trash")
def trash_page() -> None:
    _header()

    @ui.refreshable
    def listing() -> None:
        installer = state.installer
        ui.label(_("Trash")).classes("text-2xl font-bold")
        ui.label(_("Removed tools and old versions are kept here for {days} days.",
                   days=state.retention_days)).classes("text-sm text-slate-500 mb-2")
        items = installer.trash.items() if installer else []
        if not items:
            ui.label(_("The Trash is empty")).classes("text-slate-500")
            return
        for item in items:
            with ui.card().classes("w-full"), ui.row().classes("w-full items-center gap-4"):
                with ui.column().classes("gap-0 flex-grow"):
                    ui.label(item.label).classes("font-semibold")
                    reason = (_("Uninstalled") if item.reason == "uninstalled"
                              else _("Old version"))
                    ui.label(_("{reason} on {deleted}; permanently deleted after {expires}",
                               reason=reason, deleted=format_time(item.deleted_at),
                               expires=format_time(item.expires_at(state.retention_days)))
                             ).classes("text-sm text-slate-500")
                ui.button(_("Open folder"), on_click=lambda i=item: open_folder(i.path)).props(
                    "flat no-caps color=grey-8"
                )
                ui.button(_("Restore"), icon="restore", on_click=lambda i=item: restore(i.id)
                          ).props("outline")
                ui.button(_("Delete permanently"), on_click=lambda i=item: purge(i.id, i.label)
                          ).props("flat no-caps color=grey-8")

    async def restore(item_id: str) -> None:
        try:
            await ng_run.io_bound(state.installer.trash.restore, item_id)
            ui.notify(_("Restored"), type="positive")
        except FileExistsError:
            ui.notify(_("Something already exists at the original location; restore skipped."),
                      type="warning")
        except (OSError, KeyError) as e:
            ui.notify(_("Restore failed: {error}", error=e), type="negative")
        _changed()

    async def purge(item_id: str, label: str) -> None:
        if await _confirm(_("Permanently delete {name}?", name=label),
                          [_("This cannot be undone.")], _("Delete permanently")):
            try:
                await ng_run.io_bound(state.installer.trash.purge, item_id)
            except (OSError, KeyError) as e:
                ui.notify(_("Delete failed: {error}", error=e), type="negative")
            _changed()

    listing()
    _watch_generation(listing)


# -- startup ---------------------------------------------------------------------


def _setup_prompt() -> None:
    with ui.card().classes("max-w-xl"):
        ui.label(_("Welcome to ToolManager")).classes("text-xl font-semibold")
        ui.label(_("Connect to the department's tool catalog to get started. "
                   "It takes about a minute.")).classes("text-slate-600")
        ui.button(_("Set up connection"), icon="arrow_forward",
                  on_click=lambda: ui.navigate.to("/setup"))


def configure(settings: Settings) -> str | None:
    """(Re)build repository, cache and installer from settings. Returns an error message."""
    state.settings = settings
    state.retention_days = settings.trash_retention_days
    state.installer = None
    state.cache = None
    state.catalog = None
    state.problems = ()
    _changed()
    if not settings.server_configured():
        state.status = _("Catalog not configured")
        return state.status
    try:
        repo = create_repository(settings)
    except (RepositoryError, CredentialError) as e:
        log.warning("repository not available: %s", e)
        state.status = _("Catalog not configured")
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
    # Show the cached catalog immediately; sync refreshes it in the background.
    try:
        cached = cache.load_cached()
        if cached:
            _apply(cached)
    except ManifestError as e:
        log.warning("cached catalog unreadable: %s", e)
    state.status = ""
    return None


async def sync() -> None:
    if state.cache is not None:
        await _sync(state.cache)


async def _sync(cache: CatalogCache) -> None:
    state.status = _("Syncing catalog...")
    try:
        result, changed = await ng_run.io_bound(cache.sync)
    except (RepositoryError, ManifestError, OSError) as e:
        log.exception("catalog sync failed")
        state.status = _("Sync failed: {error}", error=e)
        return
    _apply(result)
    state.status = _("Catalog updated") if changed else _("Catalog is up to date")
    await _auto_update()


async def _auto_update() -> None:
    if not (state.installer and state.catalog):
        return
    for tool in state.installer.auto_update_candidates(state.catalog.tools):
        try:
            await ng_run.io_bound(state.installer.install, tool)
            log.info("auto-updated %s", tool.id)
        except InstallError as e:
            log.warning("auto-update of %s failed: %s", tool.id, e)
    _changed()


def run(
    t0: float,
    native_mode: bool = True,
    local_catalog: str | None = None,
    exit_after_ready: bool = False,
) -> None:
    setup_logging()
    settings = Settings.load(paths.settings_file(), paths.defaults_file())
    if local_catalog:
        settings.backend = "local"
        settings.local_catalog = local_catalog
        state.dev_override = True
    set_language(settings.language)

    cache_current = paths.cache_dir() / "current"
    cache_current.mkdir(parents=True, exist_ok=True)
    app.add_static_files(CATALOG_URL, cache_current)
    configure(settings)
    app.on_startup(sync)
    interval = max(settings.check_interval_hours, 1) * 3600
    app.timer(interval, sync, immediate=False)

    app.on_startup(
        lambda: log.info("startup: server ready %.2fs after start", time.perf_counter() - t0)
    )
    ready_logged = False

    def on_connect() -> None:
        nonlocal ready_logged
        if ready_logged:
            return
        ready_logged = True
        elapsed = time.perf_counter() - t0
        log.info("startup: first page served %.2fs after process start", elapsed)
        print(f"STARTUP_SECONDS={elapsed:.2f}", flush=True)
        if exit_after_ready:
            ui.timer(0.5, app.shutdown, once=True)

    app.on_connect(on_connect)

    port = native.find_open_port()
    log.info("serving UI on http://127.0.0.1:%d", port)
    ui.run(
        title=f"ToolManager {__version__}",
        host="127.0.0.1",  # never expose the local server to the network
        port=port,
        native=native_mode,
        window_size=(1200, 800) if native_mode else None,
        reload=False,
        language="zh-TW" if settings.language == "zh_TW" else "en-US",
        show_welcome_message=False,
        favicon=Path(__file__).parent / "favicon.svg",
    )


# Registers /setup and /settings; imported last because it uses the helpers above.
from . import setup_pages  # noqa: E402, F401
