from __future__ import annotations

import os
from pathlib import Path

import pytest

from toolmanager.core.trash import (
    META_FILE,
    DeleteRefused,
    Trash,
    check_deletable,
    mark_owned,
    remove_owned,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def setup(tmp_path: Path):
    managed = tmp_path / "data" / "tools"
    managed.mkdir(parents=True)
    clock = Clock()
    trash = Trash(tmp_path / "data" / "trash", [managed], retention_days=7, clock=clock)
    return trash, managed, clock


def owned_folder(parent: Path, name: str = "x") -> Path:
    folder = parent / name
    mark_owned(folder)
    (folder / "file.txt").write_text("content", "utf-8")
    return folder


def test_move_and_restore(setup) -> None:
    trash, managed, _ = setup
    folder = owned_folder(managed / "tool")
    item = trash.move(folder, label="Tool 1.0", reason="old version", tool_id="tool")
    assert not folder.exists()
    assert (item.path / "file.txt").read_text("utf-8") == "content"
    assert [i.id for i in trash.items()] == [item.id]

    assert trash.restore(item.id) == folder.resolve()
    assert (folder / "file.txt").is_file()
    assert trash.items() == []


def test_restore_never_overwrites(setup) -> None:
    trash, managed, _ = setup
    folder = owned_folder(managed / "tool")
    item = trash.move(folder, label="t", reason="r")
    owned_folder(managed / "tool")  # something new now lives at the original path
    with pytest.raises(FileExistsError):
        trash.restore(item.id)
    assert (item.path / "file.txt").is_file()


def test_purge_only_after_retention(setup) -> None:
    trash, managed, clock = setup
    item = trash.move(owned_folder(managed), label="t", reason="r")
    clock.now += 6 * 86400
    assert trash.purge_expired() == 0
    clock.now += 1 * 86400
    assert trash.purge_expired() == 1
    assert not (trash.root / item.id).exists()


def test_purge_ignores_unknown_folders(setup) -> None:
    trash, _, clock = setup
    stranger = trash.root / "something-else"
    stranger.mkdir(parents=True)
    (stranger / "keep.txt").write_text("x", "utf-8")
    clock.now += 365 * 86400
    trash.purge_expired()
    assert (stranger / "keep.txt").is_file()
    with pytest.raises(KeyError):
        trash.purge("something-else")


@pytest.mark.parametrize("item_id", ["", ".", "..", "../x", "a/b", "a\\b"])
def test_item_ids_cannot_escape(setup, item_id: str) -> None:
    trash, _, _ = setup
    with pytest.raises(KeyError):
        trash.purge(item_id)


def test_refuses_managed_root_itself(setup) -> None:
    trash, managed, _ = setup
    mark_owned(managed)
    with pytest.raises(DeleteRefused, match="outside"):
        trash.move(managed, label="t", reason="r")
    with pytest.raises(DeleteRefused, match="outside"):
        trash.move(managed / "sub" / "..", label="t", reason="r")


def test_refuses_paths_outside_managed_roots(setup, tmp_path: Path) -> None:
    trash, managed, _ = setup
    outside = owned_folder(tmp_path, "elsewhere")
    with pytest.raises(DeleteRefused, match="outside"):
        trash.move(outside, label="t", reason="r")
    with pytest.raises(DeleteRefused, match="outside"):
        trash.move(managed / ".." / "elsewhere", label="t", reason="r")
    assert (outside / "file.txt").is_file()


def test_refuses_unmarked_folder(setup) -> None:
    trash, managed, _ = setup
    folder = managed / "user-folder"
    folder.mkdir()
    with pytest.raises(DeleteRefused, match="marker"):
        trash.move(folder, label="t", reason="r")
    with pytest.raises(DeleteRefused, match="marker"):
        remove_owned(folder, [managed])
    assert folder.is_dir()


def test_refuses_link_pointing_outside(setup, tmp_path: Path) -> None:
    trash, managed, _ = setup
    target = owned_folder(tmp_path, "real-project")
    link = managed / "link"
    os.symlink(target, link, target_is_directory=True)
    with pytest.raises(DeleteRefused, match="link"):
        check_deletable(link, [managed])
    assert (target / "file.txt").is_file()


def test_failed_move_leaves_no_trace(setup, monkeypatch) -> None:
    trash, managed, _ = setup
    folder = owned_folder(managed)

    def locked(self, target):
        raise PermissionError("file in use")

    monkeypatch.setattr(Path, "rename", locked)
    with pytest.raises(PermissionError):
        trash.move(folder, label="t", reason="r")
    monkeypatch.undo()
    assert (folder / "file.txt").is_file()
    assert list(trash.root.iterdir()) == []


def test_corrupt_meta_is_not_purged(setup) -> None:
    trash, managed, clock = setup
    item = trash.move(owned_folder(managed), label="t", reason="r")
    (trash.root / item.id / META_FILE).write_text("not toml =", "utf-8")
    clock.now += 365 * 86400
    trash.purge_expired()
    assert (trash.root / item.id / "payload" / "file.txt").is_file()
