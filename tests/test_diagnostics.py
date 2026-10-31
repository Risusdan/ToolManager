from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path

from toolmanager.core import diagnostics


def test_zip_holds_summary_settings_and_logs(tmp_path: Path) -> None:
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True)
    (logs / "toolmanager.log").write_text(
        "INFO sync ok\nDEBUG headers Authorization: Bearer abc123\ntoken=xyz789 sent\n", "utf-8")
    (logs / "toolmanager.log.2026-10-30").write_text("older\n", "utf-8")
    launcher_logs = tmp_path / "ToolManager-Customer" / "logs"
    launcher_logs.mkdir(parents=True)
    (launcher_logs / "launcher.log").write_text("started 0.1.0\n", "utf-8")
    settings = tmp_path / "data" / "settings.toml"
    settings.write_text('language = "zh_TW"\n', "utf-8")

    target = diagnostics.build(
        tmp_path / "out", {"ToolManager": "0.1.0", "Installed x": "1.0.0"},
        {"settings.toml": settings, "defaults.toml": None, "missing.toml": tmp_path / "no"},
        [logs, launcher_logs, logs], now=dt.datetime(2026, 10, 31, 9, 5, 0))

    assert target.name == "toolmanager-diagnostics-20261031-090500.zip"
    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
        assert names == {"summary.txt", "settings.toml", "logs/data/toolmanager.log",
                         "logs/data/toolmanager.log.2026-10-30",
                         "logs/ToolManager-Customer/launcher.log"}
        assert "Installed x: 1.0.0" in zf.read("summary.txt").decode()
        log = zf.read("logs/data/toolmanager.log").decode()
    assert "abc123" not in log and "xyz789" not in log
    assert "Authorization: Bearer ***" in log and "token=*** sent" in log


def test_large_log_keeps_its_end(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(diagnostics, "MAX_LOG_BYTES", 100)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "toolmanager.log").write_text("".join(f"line {i}\n" for i in range(100)), "utf-8")
    target = diagnostics.build(tmp_path, {}, {}, [logs])
    with zipfile.ZipFile(target) as zf:
        text = zf.read(f"logs/{tmp_path.name}/toolmanager.log").decode()
    assert text.startswith("[... first ") and text.endswith("line 99\n")
    assert "line 0\n" not in text
