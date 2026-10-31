"""Reusable pieces: status lines, tool cards, action area, version entries, empty states."""

from __future__ import annotations

from nicegui import ui

from ..core import paths
from ..core.installer import InstalledTool, RetainedVersion, ToolStatus, UpdateState
from ..core.models import Catalog, Release, Tool, ToolType
from ..i18n import _
from .format import initials, is_newer
from .state import (
    discard_version,
    install,
    launch,
    open_folder,
    open_installer,
    open_known_issues,
    open_new_issue,
    set_auto_update,
    state,
    switch_to_previous,
    toggle_favorite,
    uninstall,
)
from .theme import pill, tile


def type_label(tool: Tool) -> str:
    return {
        ToolType.EXE: _("Program"),
        ToolType.PYTHON: _("Python script"),
        ToolType.INSTALLER: _("Installer"),
    }[tool.type]


def _important_tooltip(st: ToolStatus) -> str:
    if st.important_notes:
        return _("Recommended update: {reason}", reason=st.important_notes[0])
    return _("The author marked this update as important (e.g. a serious bug fix). "
             "See the version notes for details.")


def compact_status(tool: Tool, st: ToolStatus | None) -> None:
    """One short line: what is installed and whether an update waits. Used on cards and rows."""
    with ui.row().classes("items-center gap-1 no-wrap text-xs text-slate-500"):
        if st is None or st.state is UpdateState.NOT_INSTALLED:
            if tool.latest:
                ui.label(str(tool.latest.version))
            return
        if st.state is UpdateState.UPDATE_AVAILABLE:
            if st.important:
                ui.icon("warning_amber", size="15px").classes("text-amber-700")
            label = ui.label(_("Update {version}", version=st.latest.version)).classes(
                "text-primary font-semibold"
            )
            if st.important:
                label.tooltip(_important_tooltip(st))
            return
        ui.icon("check", size="14px")
        ui.label(_("Installed {version}", version=st.installed.current))


def favorite_button(tool: Tool) -> None:
    """Star toggle; click.stop so a click on a card's star does not open the card."""
    on = state.prefs is not None and state.prefs.is_favorite(tool.id)
    ui.button(icon="star" if on else "star_border").props(
        f"flat round dense size=sm color={'amber-7' if on else 'grey-5'}"
    ).on("click.stop", lambda: toggle_favorite(tool.id)).tooltip(
        _("Remove from favorites") if on else _("Add to favorites")
    )


def tool_card(tool: Tool, catalog: Catalog) -> None:
    """Fixed three-band card: identity, two-line summary, footer (category | status)."""
    category = catalog.category(tool.category)
    st = state.installer.status(tool) if state.installer else None
    with ui.card().props("flat bordered").classes(
        "cursor-pointer h-full p-4 gap-2 rounded-lg hover:border-slate-400 transition-colors"
    ).on("click", lambda: ui.navigate.to(f"/tool/{tool.id}")):
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            tile(initials(tool.name), 36)
            ui.label(tool.name).classes(
                "text-[15px] font-semibold truncate min-w-0 flex-grow"
            ).tooltip(tool.name)
            favorite_button(tool)
        ui.label(tool.summary).classes("text-sm text-slate-600 leading-5 h-10 line-clamp-2")
        with ui.row().classes("w-full items-center justify-between no-wrap mt-auto pt-1"):
            ui.label(category.name if category else tool.category).classes(
                "text-xs text-slate-500 truncate"
            )
            compact_status(tool, st)


def busy_indicator(tool_id: str) -> bool:
    op = state.busy.get(tool_id)
    if op is None:
        return False
    with ui.column().classes("w-56 gap-1"):
        ui.label(op.label).classes("text-xs text-slate-600")
        ui.linear_progress(show_value=False, size="6px").bind_value_from(op, "value").props(
            "rounded"
        )
    return True


def _switch_label(inst: InstalledTool) -> str:
    if is_newer(inst.previous, inst.current):
        return _("Switch back to {version}", version=inst.previous)
    return _("Roll back to {version}", version=inst.previous)


def more_menu(tool: Tool, inst: InstalledTool) -> None:
    with ui.button(icon="more_horiz").props("outline dense color=grey-7").classes("px-2"):
        with ui.menu().props("anchor='bottom right' self='top right'"):
            if inst.previous:
                ui.menu_item(_switch_label(inst), on_click=lambda: switch_to_previous(tool))
            ui.menu_item(_("Open install folder"),
                         on_click=lambda: open_folder(paths.tools_dir() / tool.id))
            if state.catalog and state.catalog.jira:
                ui.separator()
                ui.menu_item(_("Known issues"), on_click=lambda: open_known_issues(tool))
                ui.menu_item(_("Report a new issue"), on_click=lambda: open_new_issue(tool))
            ui.separator()
            ui.menu_item(_("Uninstall"), on_click=lambda: uninstall(tool.id, tool.name))


def actions(tool: Tool, st: ToolStatus | None, with_menu: bool = True) -> None:
    """Primary action (solid), secondary (outline) and the ⋯ menu for everything else."""
    if busy_indicator(tool.id) or st is None:
        return
    with ui.row().classes("items-center gap-2 no-wrap"):
        if tool.type is ToolType.INSTALLER:
            ui.button(_("Download and open installer"), icon="download",
                      on_click=lambda: open_installer(tool)).props("unelevated")
            return
        if st.state is UpdateState.NOT_INSTALLED:
            if tool.latest:
                ui.button(_("Install {version}", version=tool.latest.version), icon="download",
                          on_click=lambda: install(tool)).props("unelevated")
            return
        if st.state is UpdateState.UPDATE_AVAILABLE:
            ui.button(_("Update to {version}", version=st.latest.version), icon="upgrade",
                      on_click=lambda: install(tool)).props("unelevated")
        if tool.type is ToolType.EXE:
            ui.button(_("Open"), icon="play_arrow", on_click=lambda: launch(tool.id)).props(
                "outline color=grey-8"
            )
        elif tool.type is ToolType.PYTHON:
            ui.button(_("Run"), icon="play_arrow",
                      on_click=lambda: ui.navigate.to(f"/run/{tool.id}")).props(
                "outline color=grey-8"
            )
        if with_menu:
            more_menu(tool, st.installed)


def auto_update_switch(tool_id: str, inst: InstalledTool) -> None:
    ui.switch(_("Update automatically"), value=inst.auto_update,
              on_change=lambda e: set_auto_update(tool_id, e.value)).props("dense size=sm").classes(
        "text-xs text-slate-600"
    )


def release_entry(tool: Tool, rel: Release, st: ToolStatus | None) -> None:
    current = st.installed.current if st and st.installed else None
    is_latest = tool.latest is not None and rel.version == tool.latest.version
    with ui.column().classes("w-full gap-1 border-t border-slate-200 py-3"):
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.label(str(rel.version)).classes("font-semibold text-sm")
            if is_latest:
                pill(_("Latest"), "accent")
            if rel.version == current:
                pill(_("In use"))
            if rel.important:
                with ui.row().classes("pill bg-amber-100 text-amber-800 items-center gap-0.5"):
                    ui.icon("warning_amber", size="13px")
                    ui.label(_("Recommended"))
            ui.space()
            ui.label(rel.date.isoformat()).classes("text-xs text-slate-400")
        if rel.important and rel.important_note:
            ui.label(rel.important_note).classes("text-sm text-amber-800")
        if rel.changelog:
            ui.markdown(rel.changelog).classes("notes")
        can_install = (
            tool.type is not ToolType.INSTALLER
            and st is not None
            and not is_latest
            and rel.version != current
            and tool.id not in state.busy
        )
        if can_install:
            ui.link(_("Install this version"), "#").classes(
                "text-xs text-primary no-underline self-end"
            ).on("click.prevent", lambda r=rel: install(tool, r))


def retained_notice(name: str, kept: RetainedVersion) -> None:
    with ui.row().classes(
        "w-full items-center gap-2 bg-amber-50 rounded px-3 py-2 no-wrap text-amber-900"
    ):
        ui.icon("folder_special", size="18px")
        ui.label(
            _("Old version {version} was kept because it contains {count} file(s) you created "
              "or changed (e.g. {example}).", version=kept.version,
              count=len(kept.user_files), example=kept.user_files[0])
        ).classes("text-sm flex-grow")
        ui.button(_("Open folder"), on_click=lambda: open_folder(kept.path)).props(
            "flat dense color=grey-8"
        )
        ui.button(_("Move to Trash"),
                  on_click=lambda: discard_version(kept.tool_id, kept.version, name)).props(
            "flat dense color=grey-8"
        )


_BANNER_LOOK = {
    "info": ("tm-info", "text-primary"),
    "warning": ("bg-amber-50 border-amber-200 text-amber-900", "text-amber-800"),
}


def banner(kind: str, icon: str, text: str, action: str = "", on_click=None,
           target: str = "") -> None:
    """Full-width notice. Every banner has the same height, with or without an action:
    the action is a text link (no button padding), so banners stacked together line up."""
    look, action_look = _BANNER_LOOK[kind]
    with ui.row().classes(
        f"w-full min-h-[42px] items-center no-wrap gap-2 border rounded-lg px-3 py-2 "
        f"text-sm {look}"
    ):
        ui.icon(icon, size="18px").classes("shrink-0")
        ui.label(text).classes("flex-grow leading-5")
        if action:
            link = ui.link(action, target or "#").classes(
                f"shrink-0 font-semibold no-underline whitespace-nowrap {action_look}"
            )
            if on_click is not None:
                link.on("click.prevent", on_click)


def empty_state(icon: str, title: str, text: str = "", button: tuple[str, str] | None = None
                ) -> None:
    with ui.column().classes("w-full items-center gap-2 py-16 text-center"):
        ui.icon(icon, size="40px").classes("text-slate-300")
        ui.label(title).classes("text-base font-semibold text-slate-700")
        if text:
            ui.label(text).classes("text-sm text-slate-500 max-w-md")
        if button:
            label, target = button
            ui.button(label, on_click=lambda: ui.navigate.to(target)).props(
                "outline"
            ).classes("mt-2")
