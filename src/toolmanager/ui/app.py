"""Application entry: logging, settings, catalog configuration and the NiceGUI server."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from nicegui import app, native, ui

from .. import __version__
from ..core import icon, paths
from ..core.logging_setup import setup_logging
from ..core.settings import Settings
from ..core.single_instance import ACTIVATE_PATH, TOKEN_HEADER, InstanceLock
from ..i18n import _, set_language
from . import pages, run_page, setup_pages, theme  # noqa: F401  (registers the @ui.page routes)
from .pages import CATALOG_URL
from .state import configure, state, sync
from .tray import tray

log = logging.getLogger(__name__)

_mutex_handle = None


def _serve_activation(instance: InstanceLock, port: int, native_mode: bool) -> None:
    """A second start calls this endpoint instead of opening another window."""

    @app.post(ACTIVATE_PATH)
    async def activate(request: Request) -> JSONResponse:
        if request.headers.get(TOKEN_HEADER) != instance.token:
            return JSONResponse({"ok": False}, status_code=403)
        log.info("activated by a second start")
        bring_to_front(port, native_mode)
        return JSONResponse({"ok": True})


def _setup_tray(port: int, start_hidden: bool) -> None:
    """System tray + minimize-to-tray (Windows desktop window only)."""
    # Started at login with --background: wait in the tray without showing the window.
    app.native.window_args["hidden"] = start_hidden

    async def start() -> None:
        tray.start(asyncio.get_running_loop(), show=lambda: bring_to_front(port, True),
                   check=sync, quit_app=app.shutdown)
        if start_hidden and not tray.available:
            bring_to_front(port, True)  # no tray to come back from: show the window after all

    def minimized(_event) -> None:
        window = app.native.main_window
        if not tray.available or window is None:
            return
        window.hide()  # off the taskbar; the tray icon brings it back
        if not tray.hint_shown:
            tray.hint_shown = True
            tray.notify(_("ToolManager is still running"),
                        _("It keeps checking for updates. Click the tray icon to open it."))

    app.on_startup(start)
    app.on_shutdown(tray.stop)
    app.native.on("minimized", minimized)


def bring_to_front(port: int, native_mode: bool) -> None:
    window = getattr(app.native, "main_window", None) if native_mode else None
    if window is None:
        webbrowser.open(f"http://127.0.0.1:{port}/")  # browser mode: open a tab on it
        return
    try:
        window.show()
        window.restore()
        # Windows blocks background apps from taking the focus; a short always-on-top
        # toggle reliably brings the window forward.
        window.set_always_on_top(True)
        window.set_always_on_top(False)
    except Exception as e:  # the window may be closing; never fail the request for it
        log.warning("could not bring the window to the front: %s", e)


def _window_icon_later(accent: str) -> None:
    """Give the desktop window (and its taskbar button) the edition's icon. The window shows
    the exe's icon by default, and ToolManagerApp.exe is the same file in every edition (one
    self-update package for all), so the customer edition sets its own icon at runtime."""
    if accent == icon.BASE_COLOR:
        return  # the exe already carries the standard icon

    def work() -> None:
        try:
            ico = icon.write_ico(paths.data_root() / "cache" / f"icon-{accent[1:]}.ico", accent)
            for _attempt in range(60):  # the window process needs a moment to show its window
                if _set_window_icon(ico):
                    return
                time.sleep(0.5)
            log.warning("window icon: no window found")
        except Exception as e:  # cosmetic only: never stop ToolManager for it
            log.warning("window icon: %s", e)

    threading.Thread(target=work, name="window-icon", daemon=True).start()


def _set_window_icon(ico: Path) -> bool:
    """WM_SETICON on our window: titled "ToolManager ..." and owned by our window process
    (NiceGUI runs it as a child), so the other edition's window is never touched."""
    import ctypes
    import multiprocessing
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                    wintypes.LPARAM]
    pids = {p.pid for p in multiprocessing.active_children()}
    load = lambda metric: user32.LoadImageW(  # noqa: E731
        None, str(ico), 1, user32.GetSystemMetrics(metric), user32.GetSystemMetrics(metric),
        0x10)  # IMAGE_ICON, LR_LOADFROMFILE
    big, small = load(11), load(49)  # SM_CXICON, SM_CXSMICON
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = ctypes.create_unicode_buffer(128)
        user32.GetWindowTextW(hwnd, title, 128)
        if pid.value in pids and title.value.startswith("ToolManager"):
            user32.SendMessageW(hwnd, 0x0080, 1, big)  # WM_SETICON, ICON_BIG
            user32.SendMessageW(hwnd, 0x0080, 0, small)  # ICON_SMALL
            found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return bool(found)


def _hold_app_mutex() -> None:
    global _mutex_handle
    if sys.platform == "win32" and _mutex_handle is None:
        import ctypes

        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(
            None, False, paths.app_mutex_name(os.environ.get("TOOLMANAGER_LAUNCHER")))


def run(
    t0: float,
    native_mode: bool = True,
    local_catalog: str | None = None,
    exit_after_ready: bool = False,
    instance: InstanceLock | None = None,
    start_hidden: bool = False,
) -> None:
    setup_logging()
    _hold_app_mutex()
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
    if instance is not None:
        _serve_activation(instance, port, native_mode)
        instance.publish(port)
        state.instance = instance
    if native_mode and sys.platform == "win32":
        _setup_tray(port, start_hidden)
        app.on_startup(lambda: _window_icon_later(theme.accent()))
    ui.run(
        title=f"ToolManager {__version__}",
        host="127.0.0.1",  # never expose the local server to the network
        port=port,
        native=native_mode,
        window_size=(1200, 800) if native_mode else None,
        reload=False,
        language="zh-TW" if settings.language == "zh_TW" else "en-US",
        show_welcome_message=False,
        favicon=icon.svg(theme.accent()),
    )
