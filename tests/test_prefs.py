from __future__ import annotations

from pathlib import Path

from toolmanager.core.prefs import MAX_RECENT, Prefs


def test_favorites_persist(tmp_path: Path) -> None:
    path = tmp_path / "prefs.json"
    prefs = Prefs(path)
    assert prefs.toggle_favorite("csv-plotter") is True
    assert prefs.toggle_favorite("register-viewer") is True
    assert prefs.toggle_favorite("csv-plotter") is False
    assert Prefs(path).favorites == {"register-viewer"}
    assert not path.with_suffix(".tmp").exists()


def test_recent_is_newest_first_and_capped(tmp_path: Path) -> None:
    prefs = Prefs(tmp_path / "prefs.json")
    for i in range(MAX_RECENT + 3):
        prefs.record_use(f"tool-{i}", when=1000 + i)
    prefs.record_use("tool-0", when=5000)  # used again: back to the top
    ids = Prefs(tmp_path / "prefs.json").recent_ids()
    assert len(ids) == MAX_RECENT
    assert ids[0] == "tool-0" and ids[1] == f"tool-{MAX_RECENT + 2}"
    assert "tool-1" not in ids  # oldest dropped


def test_unreadable_file_starts_fresh(tmp_path: Path) -> None:
    path = tmp_path / "prefs.json"
    path.write_text("{not json", "utf-8")
    prefs = Prefs(path)
    assert prefs.favorites == set() and prefs.recent == {}
    prefs.toggle_favorite("x")  # and it can be written again
    assert Prefs(path).favorites == {"x"}
