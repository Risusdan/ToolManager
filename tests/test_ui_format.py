from __future__ import annotations

from packaging.version import Version

from toolmanager.core.installer import InstalledTool, ToolStatus
from toolmanager.core.models import Tool, ToolType
from toolmanager.ui.format import days_left, initials, is_newer, sort_key, strip_title_heading


def test_initials() -> None:
    assert initials("Register Viewer") == "RV"
    assert initials("CSV Plotter") == "CP"
    assert initials("Serial") == "Se"
    assert initials("韌體燒錄小幫手") == "韌"
    assert initials("3D LUT 產生器") == "3L"
    assert initials("  ") == "?"


def test_strip_title_heading() -> None:
    md = "# Register Viewer\n\nIntro\n\n## Features\n"
    assert strip_title_heading(md, "register viewer") == "Intro\n\n## Features\n"
    assert strip_title_heading("\n# Register Viewer #\nx", "Register Viewer") == "x"
    # Different first heading, or no heading: unchanged.
    assert strip_title_heading("# Overview\nx", "Register Viewer") == "# Overview\nx"
    assert strip_title_heading("Intro\n# Register Viewer", "Register Viewer").startswith("Intro")


def _tool(name: str) -> Tool:
    return Tool(id=name.lower(), name=name, summary="", category="misc", owners=("a",),
                type=ToolType.EXE)


def _status(installed: str | None, latest: str) -> ToolStatus:
    import datetime as dt

    from toolmanager.core.models import Release

    rel = Release(version=Version(latest), date=dt.date(2026, 1, 1), changelog="", entry="x")
    inst = (InstalledTool(id="x", name="x", type=ToolType.EXE, current=Version(installed))
            if installed else None)
    return ToolStatus(installed=inst, latest=rel)


def test_sort_updates_then_installed_then_rest_by_name() -> None:
    entries = [
        (_tool("beta"), _status(None, "1.0.0")),
        (_tool("Alpha"), _status(None, "1.0.0")),
        (_tool("gamma"), _status("1.0.0", "1.0.0")),
        (_tool("delta"), _status("1.0.0", "2.0.0")),
    ]
    ordered = sorted(entries, key=lambda e: sort_key(*e))
    assert [t.name for t, _ in ordered] == ["delta", "gamma", "Alpha", "beta"]


def test_is_newer_drives_switch_back_wording() -> None:
    assert is_newer(Version("1.2.0"), Version("1.1.0"))  # after a downgrade: "switch back"
    assert not is_newer(Version("1.0.0"), Version("1.1.0"))  # normal rollback


def test_days_left_rounds_up() -> None:
    day = 86400
    assert days_left(0, 7, now=0) == 7
    assert days_left(0, 7, now=6 * day + 1) == 1
    assert days_left(0, 7, now=7 * day) == 0
    assert days_left(0, 7, now=10 * day) == 0
