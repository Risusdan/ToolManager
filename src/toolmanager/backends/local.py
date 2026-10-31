"""Repository backed by a plain catalog folder. Used for development, demos and tests."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from ..core.lfs import parse_pointer
from ..core.manifest import RELEASE_FILE
from ..core.publish import Publisher
from ..core.repository import Progress, Repository, RepositoryError

CHUNK = 1024 * 256


def _is_artifact(rel: Path) -> bool:
    # tools/<id>/releases/<ver>/<file>: everything except release.toml is an artifact.
    parts = rel.parts
    return len(parts) == 5 and parts[0] == "tools" and parts[2] == "releases" and (
        parts[4] != RELEASE_FILE
    )


class LocalRepository(Repository):
    def __init__(self, root: Path) -> None:
        if not root.is_dir():
            raise RepositoryError(f"catalog folder not found: {root}")
        self.root = root

    def _catalog_files(self) -> list[Path]:
        return sorted(
            p.relative_to(self.root)
            for p in self.root.rglob("*")
            if p.is_file() and not _is_artifact(p.relative_to(self.root))
        )

    def latest_revision(self) -> str:
        h = hashlib.sha1()
        for rel in self._catalog_files():
            st = (self.root / rel).stat()
            h.update(f"{rel.as_posix()}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
        return h.hexdigest()

    def fetch_catalog(self, revision: str, dest: Path) -> None:
        for rel in self._catalog_files():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.root / rel, target)

    def download_artifact(
        self, revision: str, rel_path: str, dest: Path, progress: Progress | None = None
    ) -> Path:
        src = self.root / rel_path
        if not src.is_file():
            raise RepositoryError(f"artifact not found: {rel_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        total = src.stat().st_size
        done = 0
        with src.open("rb") as fin, dest.open("wb") as fout:
            while chunk := fin.read(CHUNK):
                fout.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        return dest


class LocalPublisher(Publisher):
    """Publishes straight into a local catalog folder (development and demos): no branch, no
    review, and packages are written as real files instead of LFS pointers."""

    uses_pull_requests = False

    def __init__(self, root: Path, user: str = "") -> None:
        self.root = root
        self.user = user or os.environ.get("USERNAME") or os.environ.get("USER") or "local"
        self._lfs: dict[str, Path] = {}

    def current_user(self) -> str:
        return self.user

    def known_user(self) -> str:
        return self.user

    def can_write(self) -> bool:
        return os.access(self.root, os.W_OK)

    def upload_lfs(self, path: Path, oid: str, size: int) -> None:
        self._lfs[oid] = path

    def create_branch(self, name: str) -> str:
        return name

    def commit_file(self, branch: str, rel_path: str, content: bytes, message: str) -> None:
        target = (self.root / rel_path).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise RepositoryError(f"refusing to write outside the catalog: {rel_path}")
        pointer = parse_pointer(content)
        target.parent.mkdir(parents=True, exist_ok=True)
        if pointer is not None and pointer.oid in self._lfs:
            shutil.copyfile(self._lfs[pointer.oid], target)
        else:
            target.write_bytes(content)

    def open_pull_request(self, branch: str, title: str, description: str) -> tuple[str, object]:
        return "", None

    def merge_pull_request(self, handle: object) -> None:
        pass
