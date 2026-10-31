"""Pages: all tools (/), tool detail, installed tools, trash."""

from __future__ import annotations

from nicegui import run as ng_run
from nicegui import ui

from ..core.installer import UpdateState
from ..core.models import Tool
from ..core.readme import rewrite_relative_images
from ..core.trash import format_time
from ..i18n import _
from .format import days_left, initials, short_date, sort_key, strip_title_heading
from .state import (
    changed,
    confirm,
    open_folder,
    state,
    status_of,
    tools_with_updates,
    uninstall,
    update_all,
)
from .state import watch as watch_state
from .theme import frame, pill, tile
from .widgets import (
    actions,
    auto_update_switch,
    compact_status,
    empty_state,
    release_entry,
    retained_notice,
    tool_card,
    type_label,
)

CATALOG_URL = "/catalog"


def _matches(tool: Tool, query: str, category: str | None) -> bool:
    if category and tool.category != category:
        return False
    if not query:
        return True
    q = query.casefold()
    return any(q in text.casefold() for text in (tool.name, tool.summary, tool.readme))


def _welcome() -> None:
    with ui.column().classes("w-full items-center py-16 gap-3 text-center"):
        ui.icon("hub", size="44px").classes("text-slate-300")
        ui.label(_("Welcome to ToolManager")).classes("text-xl font-semibold")
        ui.label(_("Connect to the department's tool catalog to get started. "
                   "It takes about a minute.")).classes("text-sm text-slate-600 max-w-md")
        ui.button(_("Set up connection"), icon="arrow_forward",
                  on_click=lambda: ui.navigate.to("/setup")).props("unelevated").classes("mt-2")


def _updates_banner() -> None:
    updates = tools_with_updates()
    if not updates:
        return
    important = sum(1 for _t, st in updates if st.important)
    text = _("{count} installed tool(s) have a new version", count=len(updates))
    if important:
        text += _(", {count} recommended by the author", count=important)
    with ui.row().classes(
        "w-full items-center no-wrap gap-2 bg-blue-50 border border-blue-200 rounded-lg "
        "px-3 py-2 text-sm text-blue-900"
    ):
        ui.icon("upgrade", size="18px")
        ui.label(text).classes("flex-grow")
        ui.link(_("Go to Installed →"), "/installed").classes(
            "text-primary font-semibold no-underline"
        )


@ui.page("/")
def home_page(category: str | None = None) -> None:
    filters = {"query": "", "category": category or None}

    def select_category(cat_id: str | None) -> None:
        filters["category"] = cat_id
        grid.refresh()
        refresh_sidebar()

    refresh_sidebar = frame("all", filters, select_category)

    @ui.refreshable
    def grid() -> None:
        catalog = state.catalog
        if catalog is None:
            if state.cache is None:
                _welcome()
            elif state.sync_status == "failed":
                empty_state("cloud_off", _("Could not load the tool catalog"), state.sync_error)
            else:
                empty_state("hourglass_empty", _("Loading the tool catalog..."))
            return
        tools = [t for t in catalog.tools if _matches(t, filters["query"], filters["category"])]
        count_label.set_text(_("{count} tools", count=len(tools)))
        _updates_banner()
        if not tools:
            empty_state("search_off", _("No tools match \"{query}\"", query=filters["query"])
                        if filters["query"] else _("No tools in this category yet"))
            return
        tools.sort(key=lambda t: sort_key(t, status_of(t)))
        with ui.element("div").classes("w-full grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 "
                                       "gap-3"):
            for tool in tools:
                tool_card(tool, catalog)
        if state.problems:
            ui.label(_("{count} tool(s) skipped due to manifest errors", count=len(state.problems))
                     ).classes("text-xs text-slate-400")

    def on_search(e) -> None:
        filters["query"] = (e.value or "").strip()
        grid.refresh()

    with ui.row().classes("w-full items-center gap-4 no-wrap").bind_visibility_from(
        state, "cache", backward=lambda cache: cache is not None
    ):
        ui.input(placeholder=_("Search name, description, README..."), on_change=on_search
                 ).props("outlined dense clearable").classes("flex-grow").add_slot(
            "prepend", '<q-icon name="search" size="18px" />'
        )
        count_label = ui.label().classes("text-xs text-slate-500 whitespace-nowrap")

    grid()
    watch_state(grid)


@ui.page("/tool/{tool_id}")
def tool_page(tool_id: str) -> None:
    tool0 = state.catalog.tool(tool_id) if state.catalog else None
    frame(None, {"category": tool0.category if tool0 else None})

    @ui.refreshable
    def detail() -> None:
        catalog = state.catalog
        tool = catalog.tool(tool_id) if catalog else None
        if tool is None:
            empty_state("search_off", _("Tool not found"), button=(_("All tools"), "/"))
            return
        category = catalog.category(tool.category)
        st = status_of(tool)

        with ui.row().classes("items-center gap-1 text-xs text-slate-500"):
            ui.link("← " + _("All tools"), "/").classes("text-primary no-underline")
            ui.label("/")
            ui.link(category.name if category else tool.category,
                    f"/?category={tool.category}").classes("text-slate-500 no-underline")

        with ui.row().classes("w-full no-wrap items-start gap-4 pb-4 border-b border-slate-200"):
            tile(initials(tool.name), 56)
            with ui.column().classes("gap-1 flex-grow min-w-0"):
                ui.label(tool.name).classes("text-[22px] font-semibold leading-tight")
                ui.label(tool.summary).classes("text-sm text-slate-600")
                meta = [_("Owners {names}", names=", ".join(tool.owners)), type_label(tool)]
                if tool.latest:
                    meta.append(_("Latest {version} ({date})", version=tool.latest.version,
                                  date=tool.latest.date.isoformat()))
                ui.label("  ·  ".join(meta)).classes("text-xs text-slate-500")
            with ui.column().classes("items-end gap-2 shrink-0"):
                actions(tool, st)
                if st and st.installed:
                    with ui.row().classes("items-center gap-3 no-wrap"):
                        with ui.row().classes("items-center gap-1 text-xs text-slate-500"):
                            ui.icon("check", size="14px")
                            ui.label(_("Installed {version}", version=st.installed.current))
                        auto_update_switch(tool.id, st.installed)

        if st and st.important and st.state is UpdateState.UPDATE_AVAILABLE:
            with ui.row().classes(
                "w-full no-wrap items-center gap-2 bg-amber-50 border border-amber-200 "
                "text-amber-900 rounded-lg px-3 py-2 text-sm"
            ):
                ui.icon("warning_amber", size="18px")
                if st.important_notes:
                    ui.label(_("Recommended update: {reason}",
                               reason="; ".join(st.important_notes)))
                else:
                    ui.label(_("The author recommends updating soon. The reason is in the notes "
                               "of the versions marked \"Recommended\" under Version history."))

        with ui.row().classes("w-full no-wrap items-start gap-10"):
            with ui.column().classes("flex-grow min-w-0"):
                readme = strip_title_heading(tool.readme, tool.name)
                readme = rewrite_relative_images(readme, f"{CATALOG_URL}/tools/{tool.id}")
                if readme.strip():
                    ui.markdown(readme).classes("readme")
                else:
                    ui.label(_("No description yet.")).classes("text-sm text-slate-500")
            with ui.column().classes("w-[300px] shrink-0 gap-0"):
                ui.label(_("Version history")).classes(
                    "text-xs font-semibold text-slate-500 pb-2"
                )
                for rel in tool.releases:
                    release_entry(tool, rel, st)

    detail()
    watch_state(detail)


@ui.page("/installed")
def installed_page() -> None:
    frame("installed")

    @ui.refreshable
    def listing() -> None:
        installer = state.installer
        if installer is None:
            _welcome()
            return
        installed = installer.list_installed()
        updates = tools_with_updates()
        with ui.row().classes("w-full items-end justify-between"):
            with ui.column().classes("gap-0"):
                ui.label(_("Installed")).classes("text-xl font-semibold")
                if installed:
                    ui.label(_("{count} tools · {updates} with updates", count=len(installed),
                               updates=len(updates))).classes("text-xs text-slate-500")
            if updates:
                ui.button(_("Update all"), icon="upgrade", on_click=update_all).props(
                    "unelevated"
                )
        if not installed:
            empty_state("inventory_2", _("No tools installed yet"),
                        _("Browse the catalog and install the tools you need."),
                        (_("Browse all tools"), "/"))
            return

        catalog = state.catalog

        def key(inst):
            tool = catalog.tool(inst.id) if catalog else None
            return sort_key(tool, status_of(tool)) if tool else (3, inst.name.casefold())

        with ui.column().classes("w-full gap-0 border border-slate-200 rounded-lg"):
            for i, inst in enumerate(sorted(installed, key=key)):
                tool = catalog.tool(inst.id) if catalog else None
                st = status_of(tool) if tool else None
                border = "border-t border-slate-200" if i else ""
                with ui.column().classes(f"w-full gap-2 px-4 py-3 {border}"):
                    with ui.element("div").classes(
                        "w-full grid grid-cols-[1fr_130px_310px] items-center gap-4"
                    ):
                        with ui.row().classes("items-center gap-3 no-wrap min-w-0"):
                            tile(initials(inst.name), 32)
                            with ui.column().classes("gap-0 min-w-0"):
                                if tool:
                                    ui.link(inst.name, f"/tool/{inst.id}").classes(
                                        "text-sm font-semibold text-slate-900 no-underline "
                                        "truncate"
                                    )
                                else:
                                    ui.label(inst.name).classes("text-sm font-semibold")
                                with ui.row().classes("items-center gap-2 no-wrap"):
                                    ui.label(_("Installed {version}", version=inst.current)
                                             ).classes("text-xs text-slate-500")
                                    if tool is None:
                                        pill(_("Removed from catalog"))
                                    elif st and st.state is UpdateState.UPDATE_AVAILABLE:
                                        compact_status(tool, st)
                        auto_update_switch(inst.id, inst)
                        with ui.row().classes("justify-end no-wrap"):
                            if tool:
                                actions(tool, st)
                            else:
                                ui.button(_("Uninstall"),
                                          on_click=lambda i=inst: uninstall(i.id, i.name)
                                          ).props("outline color=grey-8")
                    for kept in installer.retained_versions(inst.id):
                        retained_notice(inst.name, kept)

    listing()
    watch_state(listing)


@ui.page("/trash")
def trash_page() -> None:
    frame("trash")

    @ui.refreshable
    def listing() -> None:
        installer = state.installer
        with ui.column().classes("gap-0"):
            ui.label(_("Trash")).classes("text-xl font-semibold")
            ui.label(_("Removed tools and old versions are kept here for {days} days.",
                       days=state.retention_days)).classes("text-xs text-slate-500")
        items = installer.trash.items() if installer else []
        if not items:
            empty_state("delete_outline", _("The Trash is empty"),
                        _("Uninstalled tools and replaced old versions appear here and can be "
                          "restored."))
            return
        with ui.column().classes("w-full gap-0 border border-slate-200 rounded-lg"):
            for i, item in enumerate(items):
                border = "border-t border-slate-200" if i else ""
                with ui.row().classes(f"w-full items-center no-wrap gap-4 px-4 py-3 {border}"):
                    with ui.column().classes("gap-0 flex-grow min-w-0"):
                        with ui.row().classes("items-center gap-2 no-wrap"):
                            ui.label(item.label).classes("text-sm font-semibold truncate")
                            pill(_("Uninstalled") if item.reason == "uninstalled"
                                 else _("Old version"))
                        left = days_left(item.deleted_at, state.retention_days)
                        ui.label(_("Removed {date} · {days} day(s) left",
                                   date=short_date(item.deleted_at), days=left)).classes(
                            "text-xs text-slate-500"
                        ).tooltip(_("Removed {deleted}; permanently deleted after {expires}",
                                    deleted=format_time(item.deleted_at),
                                    expires=format_time(
                                        item.expires_at(state.retention_days))))
                    ui.button(_("Restore"), icon="restore",
                              on_click=lambda i=item: restore(i.id)).props("outline")
                    with ui.button(icon="more_horiz").props("outline dense color=grey-7").classes(
                        "px-2"
                    ):
                        with ui.menu().props("anchor='bottom right' self='top right'"):
                            ui.menu_item(_("Open folder"),
                                         on_click=lambda i=item: open_folder(i.path))
                            ui.separator()
                            ui.menu_item(_("Delete permanently"),
                                         on_click=lambda i=item: purge(i.id, i.label))

    async def restore(item_id: str) -> None:
        try:
            await ng_run.io_bound(state.installer.trash.restore, item_id)
            ui.notify(_("Restored"), type="positive")
        except FileExistsError:
            ui.notify(_("Something already exists at the original location; restore skipped."),
                      type="warning")
        except (OSError, KeyError) as e:
            ui.notify(_("Restore failed: {error}", error=e), type="negative")
        changed()

    async def purge(item_id: str, label: str) -> None:
        if await confirm(_("Permanently delete {name}?", name=label),
                         [_("This cannot be undone.")], _("Delete permanently")):
            try:
                await ng_run.io_bound(state.installer.trash.purge, item_id)
            except (OSError, KeyError) as e:
                ui.notify(_("Delete failed: {error}", error=e), type="negative")
            changed()

    listing()
    watch_state(listing)
