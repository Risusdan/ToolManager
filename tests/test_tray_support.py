from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from toolmanager.core import autostart
from toolmanager.core.notices import Update, UpdateNotifier
from toolmanager.i18n import set_language


@pytest.fixture(autouse=True)
def chinese() -> None:
    set_language("zh_TW")


def test_each_version_is_announced_once() -> None:
    notifier = UpdateNotifier()
    viewer = Update("register-viewer", "Register Viewer", "1.2.0")
    assert notifier.message([viewer]) == ("Register Viewer 1.2.0 已推出", "Register Viewer")
    assert notifier.message([viewer]) is None  # the 4-hourly check stays quiet
    newer = Update("register-viewer", "Register Viewer", "1.3.0")
    assert notifier.message([newer]) is not None  # a newer version is news again


def test_several_updates_and_important_ones() -> None:
    notifier = UpdateNotifier()
    title, text = notifier.message([
        Update("a", "A", "1.0.0"),
        Update("b", "B", "2.0.0", important=True),
        Update("c", "C", "3.0.0"),
        Update("d", "D", "4.0.0"),
    ])
    assert title == "4 個工具有新版本"
    assert text == "作者建議盡快更新：B"
    _, text = UpdateNotifier().message([Update(x, x, "1") for x in "ABCD"])
    assert text == "A、B、C…"


class FakeRegistry:
    """Just enough of winreg for the Run key."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def OpenKey(self, root, path, reserved=0, access=0):  # noqa: N802 - winreg's names
        assert (root, path) == ("HKCU", autostart.RUN_KEY)
        registry = self

        class Key:
            def __enter__(self):
                return registry

            def __exit__(self, *exc):
                return False

        return Key()

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_start_at_login_registry_entry() -> None:
    registry = FakeRegistry()
    launcher = Path(r"C:\Users\王 小明\AppData\Local\ToolManager\ToolManager.exe")
    assert not autostart.is_enabled(registry)
    autostart.apply(True, launcher, registry)
    assert registry.values["ToolManager"] == (
        '"C:\\Users\\王 小明\\AppData\\Local\\ToolManager\\ToolManager.exe" --background')
    assert autostart.is_enabled(registry)
    autostart.apply(False, launcher, registry)
    autostart.apply(False, launcher, registry)  # already off: no error
    assert not autostart.is_enabled(registry)


def test_start_at_login_needs_windows_and_the_launcher(monkeypatch) -> None:
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    assert not autostart.supported(Path("ToolManager.exe"))
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    assert not autostart.supported(None)  # development run without the launcher
    assert autostart.supported(Path("ToolManager.exe"))


def test_tray_stays_off_outside_windows() -> None:
    from toolmanager.ui.tray import Tray

    tray = Tray()
    tray.start(asyncio.new_event_loop(), show=lambda: None, check=lambda: None,
               quit_app=lambda: None)
    assert not tray.available
    tray.notify("t", "x")  # no-op, no error
    tray.stop()
