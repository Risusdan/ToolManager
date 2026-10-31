"""System tray icon (Windows): open, check for updates, quit; shows notifications.

Minimizing the window hides it into the tray; the window's close button quits
ToolManager (NiceGUI cannot veto a close). pystray runs on its own thread, so every
menu action is handed back to the asyncio loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable

from ..i18n import _

log = logging.getLogger(__name__)


def _icon_image():
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, size - 2, size - 2), radius=12, fill="#1e293b")
    draw.rectangle((18, 18, 46, 25), fill="white")  # a "T", matching the favicon
    draw.rectangle((28, 18, 36, 48), fill="white")
    return image


class Tray:
    def __init__(self) -> None:
        self._icon = None
        self.hint_shown = False

    @property
    def available(self) -> bool:
        return self._icon is not None

    def start(self, loop: asyncio.AbstractEventLoop, show: Callable[[], None],
              check: Callable[[], Awaitable[None]], quit_app: Callable[[], None]) -> None:
        if sys.platform != "win32":
            return
        try:
            import pystray
        except ImportError as e:
            log.warning("system tray unavailable: %s", e)
            return

        def on_loop(fn: Callable[[], object]) -> Callable[..., None]:
            return lambda *_args: loop.call_soon_threadsafe(fn)

        menu = pystray.Menu(
            pystray.MenuItem(_("Open ToolManager"), on_loop(show), default=True),
            pystray.MenuItem(_("Check for updates"),
                             lambda *_a: asyncio.run_coroutine_threadsafe(check(), loop)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(_("Quit"), on_loop(quit_app)),
        )
        self._icon = pystray.Icon("ToolManager", _icon_image(), "ToolManager", menu)
        self._icon.run_detached()

    def notify(self, title: str, text: str) -> None:
        if self._icon is None:
            return
        try:
            self._icon.notify(text, title)
        except Exception as e:  # notifications are best effort
            log.warning("notification failed: %s", e)

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
            self._icon = None


tray = Tray()
