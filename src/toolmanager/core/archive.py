"""Zip extraction helpers shared by catalog sync and tool installation."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path


class UnsafeArchiveError(Exception):
    pass


def safe_extract(zip_path: Path, dest: Path) -> None:
    """Extract zip_path into dest, refusing entries that escape dest (zip slip)."""
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(dest_resolved):
                raise UnsafeArchiveError(f"unsafe path in archive: {info.filename}")
        zf.extractall(dest)


def flatten_single_root(dest: Path) -> None:
    """If dest holds exactly one folder and nothing else, move its contents up.

    Authors often zip "MyTool/" instead of its contents; either layout installs the same.
    """
    children = list(dest.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        return
    root = children[0]
    tmp = dest / (root.name + ".__flatten__")
    root.rename(tmp)
    for item in tmp.iterdir():
        shutil.move(str(item), dest / item.name)
    tmp.rmdir()
