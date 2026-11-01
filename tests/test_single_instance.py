from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from toolmanager.core.single_instance import (
    ACTIVATE_PATH,
    INFO_FILE,
    TOKEN_HEADER,
    InstanceLock,
    activate_running,
)


def test_second_lock_is_refused_until_released(tmp_path: Path) -> None:
    first, second = InstanceLock(tmp_path), InstanceLock(tmp_path)
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_lock_held_by_another_process_and_freed_when_it_dies(tmp_path: Path) -> None:
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        from toolmanager.core.single_instance import InstanceLock
        lock = InstanceLock(Path({str(tmp_path)!r}))
        assert lock.acquire()
        print("held", flush=True)
        time.sleep(60)
    """)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert not InstanceLock(tmp_path).acquire()
    finally:
        holder.kill()  # a crash: the OS releases the lock
        holder.wait()
    lock = InstanceLock(tmp_path)
    assert lock.acquire()
    lock.release()


def test_release_removes_contact_details_and_reclaim_restores_them(tmp_path: Path) -> None:
    lock = InstanceLock(tmp_path)
    assert lock.acquire()
    lock.publish(8123)
    info = json.loads((tmp_path / INFO_FILE).read_text("utf-8"))
    assert info["port"] == 8123 and info["token"] == lock.token
    lock.release()
    assert not (tmp_path / INFO_FILE).exists()
    assert lock.reclaim()
    assert json.loads((tmp_path / INFO_FILE).read_text("utf-8"))["port"] == 8123
    lock.release()


def _server(token: str, calls: list[str]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_POST(self) -> None:
            ok = self.path == ACTIVATE_PATH and self.headers.get(TOKEN_HEADER) == token
            calls.append("ok" if ok else "denied")
            self.send_response(200 if ok else 403)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_activate_running_instance(tmp_path: Path) -> None:
    calls: list[str] = []
    server = _server("secret", calls)
    try:
        lock = InstanceLock(tmp_path)
        lock.token = "secret"
        assert lock.acquire()
        lock.publish(server.server_address[1])
        assert activate_running(tmp_path, wait_seconds=2)
        assert calls == ["ok"]
        lock.release()
    finally:
        server.shutdown()


def test_activation_needs_the_token(tmp_path: Path) -> None:
    calls: list[str] = []
    server = _server("secret", calls)
    try:
        (tmp_path / INFO_FILE).write_text(
            json.dumps({"port": server.server_address[1], "token": "guess"}), "utf-8")
        assert not activate_running(tmp_path, wait_seconds=0.5)
        assert calls and set(calls) == {"denied"}
    finally:
        server.shutdown()


def test_no_answer_gives_up_after_the_wait(tmp_path: Path) -> None:
    (tmp_path / INFO_FILE).write_text(json.dumps({"port": 9, "token": "x"}), "utf-8")
    start = time.monotonic()
    assert not activate_running(tmp_path, wait_seconds=1)
    assert time.monotonic() - start < 5
