"""Running Python script tools: form, run/stop, live log (see docs/DESIGN.md §5.3)."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from nicegui import app, ui
from nicegui import run as ng_run

from ..core import paths
from ..core.installer import InstalledTool
from ..core.models import ArgSpec, ArgType, Release, Tool, ToolType
from ..core.python_env import EnvError, venv_python
from ..core.runner import ArgError, ScriptRun, build_args, launch_console, launch_gui
from ..i18n import _
from .format import initials
from .state import (
    open_folder,
    open_known_issues,
    open_new_issue,
    record_use,
    state,
    status_of,
)
from .state import watch as watch_state
from .theme import frame, tile
from .widgets import banner, empty_state

log = logging.getLogger(__name__)

MAX_LINES = 5000


@dataclass
class RunSession:
    """One run of a tool, kept in state.runs so the log survives leaving the page."""

    version: str
    status: str = "preparing"  # preparing | running | done | failed | stopped | launched
    lines: list[str] = field(default_factory=list)
    exit_code: int | None = None
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None
    log_file: Path | None = None
    run: ScriptRun | None = None

    def add(self, line: str) -> None:
        self.lines.append(line)
        if len(self.lines) > MAX_LINES:
            del self.lines[: len(self.lines) - MAX_LINES]

    @property
    def active(self) -> bool:
        return self.status in ("preparing", "running")


def _release(tool: Tool, inst: InstalledTool) -> Release | None:
    return next((r for r in tool.releases if r.version == inst.current), None)


async def _prepare(session: RunSession, tool: Tool, inst: InstalledTool,
                   release: Release) -> Path | None:
    """Create or reuse the tool's environment; returns the venv or None on failure."""
    tool_dir = paths.tools_dir() / tool.id / str(inst.current)
    try:
        offline = bool(state.catalog and state.catalog.python_offline_only)
        return await ng_run.io_bound(state.env_manager.ensure, tool.id, str(inst.current),
                                     tool_dir, release.python, session.add, offline)
    except EnvError as e:
        session.status, session.error, session.finished = "failed", str(e), time.time()
        session.add(str(e))
        return None


async def start(tool: Tool, inst: InstalledTool, release: Release, argv: list[str],
                mode: str) -> None:
    """mode: "capture" (log panel), "console" (new window) or "gui" (pythonw)."""
    session = RunSession(version=str(inst.current))
    state.runs[tool.id] = session
    record_use(tool.id)
    venv = await _prepare(session, tool, inst, release)
    if venv is None:
        return
    tool_dir = paths.tools_dir() / tool.id / str(inst.current)
    entry = tool_dir / inst.entries[str(inst.current)]
    try:
        if mode == "gui":
            launch_gui(venv_python(venv, gui=True), entry, tool_dir)
            session.status, session.finished = "launched", time.time()
            return
        if mode == "console":
            launch_console(venv_python(venv), entry, tool_dir)
            session.status, session.finished = "launched", time.time()
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        session.log_file = paths.logs_dir() / "runs" / tool.id / f"{stamp}.log"
        display = "python " + subprocess.list2cmdline([entry.name, *argv])
        session.add("$ " + display)
        session.run = ScriptRun([str(venv_python(venv)), str(entry), *argv], tool_dir,
                                session.log_file, display)
        session.status = "running"
    except OSError as e:
        session.status, session.error, session.finished = "failed", str(e), time.time()
        session.add(_("Could not start the tool: {error}", error=e))
        return
    while session.run.running:
        for line in session.run.read_new():
            session.add(line)
        await asyncio.sleep(0.1)
    session.run.wait(5)
    for line in session.run.read_new(MAX_LINES):
        session.add(line)
    session.exit_code = session.run.returncode
    session.finished = time.time()
    if session.run.stopped:
        session.status = "stopped"
    else:
        session.status = "done" if session.exit_code == 0 else "failed"


async def _browse(field_input: ui.input, folder: bool) -> None:
    window = getattr(app.native, "main_window", None)
    if window is None:
        return
    import webview  # only available in the native (desktop) window

    kind = webview.FOLDER_DIALOG if folder else webview.OPEN_DIALOG
    result = await window.create_file_dialog(kind)
    if result:
        field_input.set_value(result[0])


def arg_field(spec: ArgSpec, values: dict) -> None:
    label = spec.label + (" *" if spec.required or spec.positional else "")
    # The form edits this dict directly; keep the last values when the page is reopened.
    values.setdefault(spec.name, bool(spec.default) if spec.type is ArgType.BOOL
                      else spec.default)
    if spec.type is ArgType.BOOL:
        ui.checkbox(label).bind_value(values, spec.name)
        if spec.help:
            ui.label(spec.help).classes("text-xs text-slate-500 -mt-2 ml-10")
        return
    props = "outlined dense"
    if spec.type is ArgType.CHOICE:
        element = ui.select(list(spec.choices), label=label).bind_value(values, spec.name)
    elif spec.type in (ArgType.INT, ArgType.FLOAT):
        element = ui.number(label).bind_value(values, spec.name)
    else:
        element = ui.input(label).bind_value(values, spec.name)
    element.props(props).classes("w-full")
    if spec.help:
        element.props(f'hint="{spec.help}"')
    if spec.type in (ArgType.FILE, ArgType.FOLDER) and getattr(app.native, "main_window", None):
        with element.add_slot("append"):
            ui.button(icon="folder_open",
                      on_click=lambda e=element: _browse(e, spec.type is ArgType.FOLDER)).props(
                "flat dense round"
            ).tooltip(_("Browse..."))


def _context(tool_id: str):
    """What the page is built from; when it changes (e.g. the tool was updated elsewhere),
    the page reloads instead of rebuilding the form under the user's cursor."""
    tool = state.catalog.tool(tool_id) if state.catalog else None
    st = status_of(tool) if tool else None
    inst = st.installed if st else None
    release = _release(tool, inst) if tool and inst else None
    usable = (tool is not None and tool.type is ToolType.PYTHON and release is not None
              and state.env_manager is not None)
    key = (usable, str(inst.current) if inst else None, state.env_manager is not None)
    return tool, st, inst, release, key


@ui.page("/run/{tool_id}")
def run_page(tool_id: str) -> None:
    tool, st, inst, release, page_key = _context(tool_id)
    frame(None, {"category": tool.category if tool else None})

    def reload_if_context_changed() -> None:
        now = _context(tool_id)[4]
        if now != page_key:
            log.info("run page context changed %s -> %s: reloading", page_key, now)
            ui.navigate.reload()

    watch_state(_Reloader(reload_if_context_changed))

    if tool is None or tool.type is not ToolType.PYTHON or inst is None or release is None:
        empty_state("terminal", _("This Python tool is not installed"),
                    button=(_("All tools"), "/"))
        return

    with ui.row().classes("items-center gap-1 text-xs text-slate-500"):
        ui.link("← " + tool.name, f"/tool/{tool.id}").classes("text-primary no-underline")

    with ui.column().classes("w-full gap-3"):
        with ui.row().classes("w-full items-center gap-4 no-wrap pb-4 border-b border-slate-200"):
            tile(initials(tool.name), 48)
            with ui.column().classes("gap-0 flex-grow min-w-0"):
                ui.label(_("Run {name}", name=tool.name)).classes("text-xl font-semibold")
                ui.label(_("Version {version}", version=inst.current)).classes(
                    "text-xs text-slate-500"
                )
            if state.catalog and state.catalog.jira:
                ui.button(_("Known issues"), icon="list_alt",
                          on_click=lambda: open_known_issues(tool)).props("flat color=grey-8")
                ui.button(_("Report a new issue"), icon="bug_report",
                          on_click=lambda: open_new_issue(tool)).props("flat color=grey-8")
        if state.env_manager is None:
            banner("warning", "warning_amber", state.runtime_problem)
            return

        values = state.run_values.setdefault(tool.id, {})
        captured = bool(release.args) or sys.platform != "win32"

        def busy() -> RunSession | None:
            session = state.runs.get(tool.id)
            active = session is not None and session.active and session.version == str(
                inst.current)
            return session if active else None

        async def run_capture() -> None:
            try:
                argv = build_args(release.args, values)
            except ArgError as e:
                ui.notify(str(e), type="warning")
                return
            await start(tool, inst, release, argv, "capture")

        # The form is built once; only the buttons and status below re-render.
        if release.gui:
            ui.label(_("This tool opens its own window.")).classes("text-sm text-slate-600")
        elif release.args:
            with ui.card().props("flat bordered").classes("w-full max-w-2xl rounded-lg gap-3"):
                for spec in release.args:
                    arg_field(spec, values)
        elif not captured:
            ui.label(_("This tool runs in its own console window, so it can ask you for "
                       "input.")).classes("text-sm text-slate-600")

        @ui.refreshable
        def buttons() -> None:
            running = busy()
            with ui.row().classes("items-center gap-2"):
                if release.gui:
                    ui.button(_("Launch"), icon="open_in_new", on_click=lambda: start(
                        tool, inst, release, [], "gui")).props("unelevated").set_enabled(
                        running is None)
                elif not captured:
                    ui.button(_("Run in a new window"), icon="terminal", on_click=lambda: start(
                        tool, inst, release, [], "console")).props("unelevated").set_enabled(
                        running is None)
                else:
                    ui.button(_("Run"), icon="play_arrow", on_click=run_capture).props(
                        "unelevated"
                    ).set_enabled(running is None)
                    if running is not None and running.run is not None:
                        ui.button(_("Stop"), icon="stop", on_click=running.run.stop).props(
                            "outline color=grey-8"
                        )

        buttons()

        @ui.refreshable
        def status_line() -> None:
            session: RunSession | None = state.runs.get(tool_id)
            if session is None:
                return
            with ui.row().classes("w-full items-center gap-2 text-sm"):
                if session.status == "preparing":
                    ui.spinner(size="16px")
                    ui.label(_("Preparing the Python environment..."))
                elif session.status == "running":
                    ui.spinner(size="16px")
                    ui.label(_("Running..."))
                elif session.status == "done":
                    ui.icon("check_circle", size="18px").classes("text-green-700")
                    ui.label(_("Finished in {seconds:.1f} s", seconds=session.finished -
                               session.started))
                elif session.status == "stopped":
                    ui.icon("stop_circle", size="18px").classes("text-slate-500")
                    ui.label(_("Stopped"))
                elif session.status == "launched":
                    ui.icon("open_in_new", size="18px").classes("text-slate-500")
                    ui.label(_("Started in its own window"))
                else:
                    ui.icon("error", size="18px").classes("text-amber-700")
                    ui.label(_("Failed (exit code {code})", code=session.exit_code)
                             if session.exit_code is not None else _("Failed"))
                ui.space()
                if session.log_file is not None and session.log_file.exists():
                    ui.link(_("Open log folder"), "#").classes(
                        "text-xs text-primary no-underline"
                    ).on("click.prevent", lambda: open_folder(session.log_file.parent))

        status_line()
        log_view = ui.log(max_lines=MAX_LINES).classes(
            "w-full h-80 bg-slate-900 text-slate-100 font-mono text-xs rounded-lg p-3"
        )

    shown = {"session": None, "count": 0, "status": None}

    def pump() -> None:
        session = state.runs.get(tool_id)
        if session is not shown["session"]:
            log_view.clear()
            shown.update(session=session, count=0)
        if session is None:
            return
        new = session.lines[shown["count"]:]
        if len(session.lines) < shown["count"]:  # trimmed: show what is kept
            log_view.clear()
            new = session.lines
        for line in new:
            log_view.push(line)
        shown["count"] = len(session.lines)
        if shown["status"] != session.status:
            shown["status"] = session.status
            buttons.refresh()
            status_line.refresh()

    ui.timer(0.2, pump)


class _Reloader:
    """Adapter so watch() can call a plain function like a refreshable."""

    def __init__(self, fn) -> None:
        self.refresh = fn
