"""Application entry: logging, settings, catalog configuration and the NiceGUI server."""

from __future__ import annotations

import logging
import sys
import time
import webbrowser
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from nicegui import app, native, ui

from .. import __version__
from ..core import paths
from ..core.logging_setup import setup_logging
from ..core.settings import Settings
from ..core.single_instance import ACTIVATE_PATH, TOKEN_HEADER, InstanceLock
from ..i18n import set_language
from . import pages, run_page, setup_pages  # noqa: F401  (registers the @ui.page routes)
from .pages import CATALOG_URL
from .state import configure, state, sync

log = logging.getLogger(__name__)

# Named mutex the installer/uninstaller checks (AppMutex in installer/ToolManager.iss) so they
# can ask the user to close ToolManager before replacing or removing its files.
APP_MUTEX = "ToolManagerAppMutex"
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


def _hold_app_mutex() -> None:
    global _mutex_handle
    if sys.platform == "win32" and _mutex_handle is None:
        import ctypes

        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX)


def run(
    t0: float,
    native_mode: bool = True,
    local_catalog: str | None = None,
    exit_after_ready: bool = False,
    instance: InstanceLock | None = None,
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
