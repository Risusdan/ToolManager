from __future__ import annotations

import multiprocessing
import sys

from toolmanager import main as entry


def test_window_child_process_does_nothing(monkeypatch) -> None:
    """NiceGUI's window process re-imports the entry module; it must not lock, configure
    or clean anything (that already happened in the application process)."""

    class Child:
        name = "SpawnProcess-1"

    monkeypatch.setattr(multiprocessing, "current_process", lambda: Child())
    monkeypatch.setattr(sys, "argv", ["toolmanager"])

    def forbidden(*args, **kwargs):
        raise AssertionError("must not run in the window process")

    monkeypatch.setattr("toolmanager.core.single_instance.InstanceLock.acquire", forbidden)
    entry.main()  # returns without touching the lock or starting the app


def test_main_process_takes_the_instance_lock(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TOOLMANAGER_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["toolmanager"])
    calls: list[str] = []

    def fake_acquire(self) -> bool:
        calls.append("acquire")
        return False  # pretend another instance runs

    monkeypatch.setattr("toolmanager.core.single_instance.InstanceLock.acquire", fake_acquire)
    monkeypatch.setattr("toolmanager.core.single_instance.activate_running",
                        lambda *a, **k: calls.append("activate") or True)
    entry.main()  # another instance answered: return quietly (exit code 0)
    assert calls == ["acquire", "activate"]
