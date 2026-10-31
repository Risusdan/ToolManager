"""Application entry: logging, settings, catalog configuration and the NiceGUI server."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from nicegui import app, native, ui

from .. import __version__
from ..core import paths
from ..core.logging_setup import setup_logging
from ..core.settings import Settings
from ..i18n import set_language
from . import pages, setup_pages  # noqa: F401  (registers the @ui.page routes)
from .pages import CATALOG_URL
from .state import configure, state, sync

log = logging.getLogger(__name__)


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
