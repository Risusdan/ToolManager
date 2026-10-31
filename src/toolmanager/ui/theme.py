"""Typography and the frame shared by every page: header, category sidebar, sync status."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable

from nicegui import ui

from ..core import icon
from ..i18n import _
from .format import short_date
from .state import restart_to_update, state, sync, tools_with_updates, watch

log = logging.getLogger(__name__)

ACCENT = icon.BASE_COLOR  # the colour the icon is drawn in
# Pseudo categories in the sidebar (tool ids never start with "@").
FAVORITES = "@favorites"
RECENT = "@recent"

CSS = """
body {
  font-family: "Segoe UI", "Microsoft JhengHei UI", "Microsoft JhengHei", "Noto Sans TC",
    "PingFang TC", sans-serif;
  color: #0f172a;
  line-height: 1.6;
  background: #fff;
}
.q-btn { text-transform: none; letter-spacing: normal; }
.q-field__label, .q-item__label { letter-spacing: normal; }

/* README and release notes: content typography, always below the page title. */
.readme { font-size: 14px; line-height: 1.75; max-width: 68ch; color: #1e293b; }
.readme h1 { font-size: 20px; font-weight: 600; line-height: 1.4; margin: 1.25em 0 .5em; }
.readme h2 { font-size: 16px; font-weight: 600; line-height: 1.4; margin: 1.25em 0 .5em; }
.readme h3, .readme h4 { font-size: 14px; font-weight: 600; margin: 1em 0 .4em; }
.readme > :first-child { margin-top: 0; }
.readme p { margin: 0 0 .75em; }
.readme ul, .readme ol { padding-left: 1.25em; margin: 0 0 .75em; }
.readme li { margin: .15em 0; }
.readme img { max-width: 100%; border: 1px solid #e2e8f0; border-radius: 6px; }
.readme code { background: #f1f5f9; padding: 0 4px; border-radius: 4px; font-size: 13px; }
.readme pre { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
  padding: 8px 12px; overflow-x: auto; }
.readme a { color: var(--tm-accent); }
.logo svg { width: 24px; height: 24px; display: block; }
.tm-header { border-top: 3px solid var(--tm-accent); }
.border-accent { border-color: var(--tm-accent) !important; }
.tm-soft { background-color: var(--tm-soft); color: var(--tm-text); }
.tm-info { background-color: var(--tm-soft); border-color: var(--tm-border); color: var(--tm-ink); }

.notes { font-size: 13px; line-height: 1.6; color: #334155; }
.notes p { margin: 0 0 .25em; }
.notes ul, .notes ol { padding-left: 1.1em; margin: 0; }

.tile { background: #f1f5f9; color: #475569; font-weight: 600; border-radius: 8px;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.pill { font-size: 12px; line-height: 18px; padding: 0 6px; border-radius: 4px;
  white-space: nowrap; }
"""


def accent() -> str:
    color = state.settings.accent.strip()
    if not color:
        return ACCENT
    if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        return color.lower()
    log.warning("ignoring accent %r: expected #rrggbb", color)
    return ACCENT


def _mix(color: str, other: str, amount: float) -> str:
    """color with `amount` of itself and the rest of `other` (both #rrggbb)."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    mixed = (round(x * amount + y * (1 - amount)) for x, y in zip(a, b, strict=True))
    return "#" + "".join(f"{v:02x}" for v in mixed)


def palette_css(color: str) -> str:
    """Tints and shades of the main colour for selections, accent pills and info banners
    (the same mixes of Tailwind's blue-600 give its blue-50/200/700/900)."""
    white, black = "#ffffff", "#000000"
    return (f":root {{ --tm-accent: {color}; --tm-soft: {_mix(color, white, 0.08)}; "
            f"--tm-border: {_mix(color, white, 0.3)}; --tm-text: {_mix(color, black, 0.8)}; "
            f"--tm-ink: {_mix(color, black, 0.55)}; }}\n")


def tile(name_initials: str, size: int) -> None:
    ui.label(name_initials).classes("tile").style(
        f"width:{size}px;height:{size}px;font-size:{max(12, size // 3)}px"
    )


def pill(text: str, kind: str = "neutral") -> ui.label:
    classes = {
        "neutral": "bg-slate-100 text-slate-600",
        "accent": "tm-soft",
        "warning": "bg-amber-100 text-amber-800",
    }[kind]
    return ui.label(text).classes(f"pill {classes}")


def _nav_link(text: str, target: str, active: bool, badge: int = 0) -> None:
    look = ("border-accent text-slate-900 font-semibold" if active
            else "border-transparent text-slate-600 hover:text-slate-900")
    with ui.link(target=target).classes(
        f"h-12 flex items-center gap-1.5 border-b-2 no-underline text-sm {look}"
    ):
        ui.label(text)
        if badge:
            ui.label(str(badge)).classes(
                "pill bg-primary text-white rounded-full min-w-[18px] text-center"
            )


def frame(
    active: str | None = None,
    selection: dict | None = None,
    on_category: Callable[[str | None], None] | None = None,
):
    """Header and category sidebar; returns the sidebar's refresh function.

    active: "all" | "installed" | "trash" | None (detail and settings pages).
    selection: {"category": id or None}; read on every render so in-page filters stay in sync.
    """
    selection = selection if selection is not None else {"category": None}
    ui.colors(primary=accent())
    ui.add_css(CSS + palette_css(accent()))
    ui.query(".nicegui-content").classes("p-6 gap-4")

    # The main colour shows in the top strip and the logo: enough to tell the customer
    # edition (its own accent) from the internal one at a glance.
    with ui.header(elevated=False).classes(
        "tm-header bg-white text-slate-900 border-b border-slate-200 "
        "px-4 py-0 h-12 items-center"
    ):
        with ui.link(target="/").classes("flex items-center gap-2 no-underline mr-4"):
            ui.html(icon.svg(accent()), sanitize=False).classes("logo")
            ui.label("ToolManager").classes("text-base font-semibold text-slate-900")

        @ui.refreshable
        def nav() -> None:
            with ui.row().classes("gap-5 items-center"):
                _nav_link(_("All tools"), "/", active == "all")
                _nav_link(_("Installed"), "/installed", active == "installed",
                          len(tools_with_updates()))
                _nav_link(_("Trash"), "/trash", active == "trash")

        nav()
        ui.space()

        @ui.refreshable
        def sync_indicator() -> None:
            if state.sync_status == "syncing":
                with ui.row().classes("items-center gap-2 text-xs text-slate-500"):
                    ui.spinner(size="14px")
                    ui.label(_("Syncing catalog..."))
            elif state.sync_status == "failed":
                ui.button(_("Sync failed · retry"), icon="sync_problem", on_click=sync).props(
                    "flat dense size=sm color=amber-9"
                ).tooltip(state.sync_error)

        sync_indicator()

        @ui.refreshable
        def self_update_indicator() -> None:
            if state.self_status == "downloading":
                with ui.row().classes("items-center gap-2 text-xs text-slate-500 mr-2"):
                    ui.spinner(size="14px")
                    ui.label(_("Downloading ToolManager {version}...",
                               version=state.self_version))
            elif state.self_status == "ready":
                ui.button(_("Restart to update to {version}", version=state.self_version),
                          icon="restart_alt", on_click=restart_to_update).props(
                    "unelevated dense size=sm"
                ).classes("px-3 mr-2")

        self_update_indicator()
        ui.button(icon="settings", on_click=lambda: ui.navigate.to("/settings")).props(
            "flat round dense color=grey-7"
        ).tooltip(_("Settings"))

    with ui.left_drawer(value=True, bordered=True).props("width=208").classes(
        "bg-slate-50 p-0 flex flex-col"
    ):
        @ui.refreshable
        def categories() -> None:
            ui.label(_("Categories")).classes("text-xs text-slate-500 px-4 pt-4 pb-1")
            catalog = state.catalog
            tools = catalog.tools if catalog else ()
            prefs = state.prefs
            ids = {t.id for t in tools}
            favorites = len(prefs.favorites & ids) if prefs else 0
            recent = len([t for t in prefs.recent_ids() if t in ids]) if prefs else 0
            items = [(None, _("All tools"), len(tools)),
                     (FAVORITES, _("Favorites"), favorites),
                     (RECENT, _("Recently used"), recent)]
            if catalog:
                items += [(c.id, c.name, sum(t.category == c.id for t in tools))
                          for c in catalog.categories]
            with ui.list().props("dense").classes("w-full px-2"):
                for cat_id, name, count in items:
                    # "All tools" is only highlighted on the all-tools page itself.
                    selected = selection["category"] == cat_id and (
                        active == "all" or cat_id is not None
                    )

                    def go(cat_id=cat_id) -> None:
                        if on_category is not None:
                            on_category(cat_id)
                        else:
                            ui.navigate.to(f"/?category={cat_id}" if cat_id else "/")

                    look = ("tm-soft font-semibold border-l-[3px] border-accent"
                            if selected else "text-slate-700 border-l-[3px] border-transparent")
                    with ui.item(on_click=go).classes(f"rounded-r min-h-[34px] {look}"):
                        with ui.item_section():
                            ui.label(name).classes("text-sm")
                        with ui.item_section().props("side"):
                            ui.label(str(count)).classes("text-xs text-slate-400")
                    if cat_id == RECENT:  # personal shortcuts above, categories below
                        ui.separator().classes("my-1")

        categories()
        ui.space()
        if state.publisher is not None:
            ui.link(_("Publish a tool"), "/publish").classes(
                "text-xs text-slate-500 no-underline px-4 pb-1 hover:text-slate-900")

        @ui.refreshable
        def synced() -> None:
            if state.synced_at and state.sync_status == "":
                ui.label(_("Catalog synced · {time}", time=_time(state.synced_at))).classes(
                    "text-xs text-slate-400 px-4 pb-3"
                )

        synced()

    watch(nav, sync_indicator, self_update_indicator, categories, synced)
    return categories.refresh


def _time(ts: float) -> str:
    t = time.localtime(ts)
    today = time.localtime()
    hm = f"{t.tm_hour:02d}:{t.tm_min:02d}"
    return hm if t[:3] == today[:3] else f"{short_date(ts)} {hm}"
