"""Repository backed by a plain catalog folder. Used for development, demos and tests."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from ..core.manifest import RELEASE_FILE
from ..core.repository import Repository, RepositoryError


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

    def download_artifact(self, revision: str, rel_path: str, dest: Path) -> Path:
        src = self.root / rel_path
        if not src.is_file():
            raise RepositoryError(f"artifact not found: {rel_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest
