"""Minimal P0 UI: catalog browse/search and tool detail pages."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from nicegui import app, native, ui
from nicegui import run as ng_run

from .. import __version__
from ..backends import create_repository
from ..core import paths
from ..core.catalog_cache import CatalogCache
from ..core.logging_setup import setup_logging
from ..core.manifest import ManifestError
from ..core.models import Catalog, Tool
from ..core.readme import rewrite_relative_images
from ..core.repository import RepositoryError
from ..core.settings import Settings
from ..i18n import _, set_language

log = logging.getLogger(__name__)

CATALOG_URL = "/catalog"


@dataclass
class AppState:
    catalog: Catalog | None = None
    problems: tuple[ManifestError, ...] = ()
    status: str = ""
    # Bumped whenever the catalog changes so open pages know to re-render.
    generation: int = 0


state = AppState()


def _apply(result) -> None:
    state.catalog = result.catalog
    state.problems = result.problems
    state.generation += 1
    for p in result.problems:
        log.warning("manifest problem: %s", p)


def _matches(tool: Tool, query: str, category: str | None) -> bool:
    if category and tool.category != category:
        return False
    if not query:
        return True
    q = query.lower()
    return q in tool.name.lower() or q in tool.summary.lower() or q in tool.readme.lower()


def _watch_generation(render) -> None:
    seen = state.generation

    def check() -> None:
        nonlocal seen
        if seen != state.generation:
            seen = state.generation
            render.refresh()

    ui.timer(1.0, check)


def _header() -> None:
    with ui.header().classes("items-center justify-between bg-slate-800"):
        ui.link("ToolManager", "/").classes("text-xl font-bold text-white no-underline")
        ui.label().bind_text_from(state, "status").classes("text-sm text-slate-300")


@ui.page("/")
def index_page() -> None:
    _header()
    filters = {"query": "", "category": None}

    @ui.refreshable
    def tool_grid() -> None:
        catalog = state.catalog
        if catalog is None:
            ui.label(state.status or _("Catalog not configured")).classes("text-slate-500")
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
    _watch_generation(tool_grid)
    _watch_generation(category_list)


def _tool_card(tool: Tool, catalog: Catalog) -> None:
    category = catalog.category(tool.category)
    with ui.card().classes("cursor-pointer hover:shadow-lg").on(
        "click", lambda t=tool: ui.navigate.to(f"/tool/{t.id}")
    ):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(tool.name).classes("text-lg font-semibold")
            ui.badge(category.name if category else tool.category).props("outline")
        ui.label(tool.summary).classes("text-slate-600")
        latest = tool.latest
        ui.label(f"{_('Latest')}: {latest.version}" if latest else _("No releases")).classes(
            "text-xs text-slate-500"
        )


@ui.page("/tool/{tool_id}")
def tool_page(tool_id: str) -> None:
    _header()

    @ui.refreshable
    def detail() -> None:
        tool = state.catalog.tool(tool_id) if state.catalog else None
        ui.button(_("Back"), icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
            "flat no-caps"
        )
        if tool is None:
            ui.label(_("No tools found"))
            return
        ui.label(tool.name).classes("text-2xl font-bold")
        ui.label(tool.summary).classes("text-slate-600")
        ui.label(f"{_('Owners')}: {', '.join(tool.owners)}").classes("text-sm text-slate-500")
        with ui.row().classes("w-full gap-8 items-start no-wrap"):
            with ui.column().classes("flex-grow"):
                readme = rewrite_relative_images(tool.readme, f"{CATALOG_URL}/tools/{tool.id}")
                ui.markdown(readme).classes("w-full")
            with ui.column().classes("w-80 shrink-0"):
                ui.label(_("Version history")).classes("text-lg font-semibold")
                for rel in tool.releases:
                    with ui.card().classes("w-full"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(str(rel.version)).classes("font-semibold")
                            ui.label(rel.date.isoformat()).classes("text-xs text-slate-500")
                            if rel.important:
                                ui.badge(_("Important"), color="red")
                        ui.markdown(rel.changelog).classes("text-sm")

    detail()
    _watch_generation(detail)


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


def run(
    t0: float,
    native_mode: bool = True,
    local_catalog: str | None = None,
    exit_after_ready: bool = False,
) -> None:
    setup_logging()
    settings = Settings.load(paths.settings_file())
    if local_catalog:
        settings.backend = "local"
        settings.local_catalog = local_catalog
    set_language(settings.language)

    cache: CatalogCache | None = None
    try:
        cache = CatalogCache(create_repository(settings), paths.cache_dir())
    except RepositoryError as e:
        log.warning("repository not available: %s", e)
        state.status = _("Catalog not configured")

    if cache is not None:
        cache.current_dir.mkdir(parents=True, exist_ok=True)
        app.add_static_files(CATALOG_URL, cache.current_dir)
        # Show the cached catalog immediately; refresh in the background.
        try:
            cached = cache.load_cached()
            if cached:
                _apply(cached)
        except ManifestError as e:
            log.warning("cached catalog unreadable: %s", e)
        app.on_startup(lambda: _sync(cache))

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

