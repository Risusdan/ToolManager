"""ToolManager's own recycle area. Every deletion of tool files goes through here.

Nothing is deleted in place: a folder is first *moved* into the trash (an atomic
rename that fails cleanly on Windows if a file is locked) and is only purged
after the retention period. Items can be restored until then.

Guards applied before anything is moved (see docs/DESIGN.md §6.3):
- the path must lie strictly inside one of the managed roots (never a root itself)
- the folder must carry a ToolManager ownership marker, so folders we did not
  create are never touched, whatever path a bug might compute
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import shutil
import stat
import time
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import tomli_w

log = logging.getLogger(__name__)

# Present in every folder ToolManager creates and may later remove.
OWNER_MARKER = ".tm-owned"
# Version folders are complete installs; they also count as owned.
COMPLETE_MARKER = ".complete"
META_FILE = "meta.toml"
PAYLOAD = "payload"


class DeleteRefused(Exception):
    """A guard rejected the path; nothing was changed."""


@dataclass(frozen=True)
class TrashItem:
    id: str
    label: str
    reason: str
    tool_id: str
    original: Path
    deleted_at: float
    path: Path  # the payload inside the trash

    def expires_at(self, retention_days: int) -> float:
        return self.deleted_at + retention_days * 86400


def mark_owned(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / OWNER_MARKER).touch()


def is_owned(folder: Path) -> bool:
    return (folder / OWNER_MARKER).is_file() or (folder / COMPLETE_MARKER).is_file()


def _real(path: Path) -> Path:
    """Resolve the parent but not the last component, so a link itself is judged, not its target."""
    path = Path(os.path.abspath(path))
    return path.parent.resolve() / path.name


def check_deletable(path: Path, roots: list[Path]) -> None:
    """Raise DeleteRefused unless path is an owned folder strictly inside a managed root."""
    real = _real(path)
    if not any(real != r and real.is_relative_to(r) for r in (x.resolve() for x in roots)):
        raise DeleteRefused(f"outside managed folders: {path}")
    if real.is_symlink() or os.path.isjunction(real):
        raise DeleteRefused(f"refusing to remove a link: {path}")
    if not real.is_dir():
        raise DeleteRefused(f"not a folder: {path}")
    if not is_owned(real):
        raise DeleteRefused(f"not created by ToolManager (no marker): {path}")


def _make_writable_and_retry(func, path, _exc) -> None:
    # Read-only files (common on Windows) make rmtree fail; clear the flag and retry once.
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError:
        pass


def remove_owned(path: Path, roots: list[Path]) -> None:
    """Delete immediately. Only for scratch folders (staging) that never hold user data."""
    check_deletable(path, roots)
    shutil.rmtree(path, onexc=_make_writable_and_retry)


class Trash:
    def __init__(
        self,
        root: Path,
        managed_roots: list[Path],
        retention_days: int = 7,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = root
        self.managed_roots = managed_roots
        self.retention_days = retention_days
        self.clock = clock

    def move(self, path: Path, *, label: str, reason: str, tool_id: str = "") -> TrashItem:
        """Move an owned folder into the trash. Raises DeleteRefused or OSError (e.g. locked)."""
        check_deletable(path, self.managed_roots)
        item_id = f"{int(self.clock())}-{uuid.uuid4().hex[:8]}"
        item_dir = self.root / item_id
        item_dir.mkdir(parents=True)
        now = self.clock()
        meta = {
            "label": label,
            "reason": reason,
            "tool_id": tool_id,
            "original": str(_real(path)),
            "deleted_at": now,
        }
        (item_dir / META_FILE).write_bytes(tomli_w.dumps(meta).encode("utf-8"))
        try:
            path.rename(item_dir / PAYLOAD)
        except OSError:
            # Undo only what we just created: one file and one now-empty folder.
            (item_dir / META_FILE).unlink()
            item_dir.rmdir()
            raise
        log.info("moved to trash: %s (%s)", path, reason)
        return TrashItem(item_id, label, reason, tool_id, Path(meta["original"]), now,
                         item_dir / PAYLOAD)

    def items(self) -> list[TrashItem]:
        if not self.root.is_dir():
            return []
        result = []
        for item_dir in self.root.iterdir():
            item = self._load(item_dir)
            if item is not None:
                result.append(item)
        return sorted(result, key=lambda i: i.deleted_at, reverse=True)

    def restore(self, item_id: str) -> Path:
        item = self._get(item_id)
        if item.original.exists():
            raise FileExistsError(str(item.original))
        item.original.parent.mkdir(parents=True, exist_ok=True)
        item.path.rename(item.original)
        self._remove_item_dir(self.root / item_id)
        log.info("restored from trash: %s", item.original)
        return item.original

    def purge(self, item_id: str) -> None:
        """Permanently delete one item (user-initiated)."""
        self._get(item_id)
        self._remove_item_dir(self.root / item_id)

    def purge_expired(self) -> int:
        now = self.clock()
        count = 0
        for item in self.items():
            if item.expires_at(self.retention_days) <= now:
                self._remove_item_dir(self.root / item.id)
                count += 1
        return count

    # -- internals -------------------------------------------------------------

    def _get(self, item_id: str) -> TrashItem:
        if not item_id or "/" in item_id or "\\" in item_id or item_id in (".", ".."):
            raise KeyError(item_id)
        item = self._load(self.root / item_id)
        if item is None:
            raise KeyError(item_id)
        return item

    def _load(self, item_dir: Path) -> TrashItem | None:
        meta_path = item_dir / META_FILE
        if not meta_path.is_file():
            return None  # unknown content: never touched by purge
        try:
            with meta_path.open("rb") as f:
                meta = tomllib.load(f)
            return TrashItem(
                id=item_dir.name,
                label=meta["label"],
                reason=meta["reason"],
                tool_id=meta.get("tool_id", ""),
                original=Path(meta["original"]),
                deleted_at=float(meta["deleted_at"]),
                path=item_dir / PAYLOAD,
            )
        except (tomllib.TOMLDecodeError, KeyError, ValueError) as e:
            log.warning("unreadable trash item %s: %s", item_dir, e)
            return None

    def _remove_item_dir(self, item_dir: Path) -> None:
        # Only direct children of the trash root that carry our metadata.
        if item_dir.parent.resolve() != self.root.resolve() or not (item_dir / META_FILE).is_file():
            raise DeleteRefused(f"not a trash item: {item_dir}")
        if (item_dir / PAYLOAD).is_symlink() or os.path.isjunction(item_dir / PAYLOAD):
            (item_dir / PAYLOAD).unlink()
        shutil.rmtree(item_dir, onexc=_make_writable_and_retry)


def format_time(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
