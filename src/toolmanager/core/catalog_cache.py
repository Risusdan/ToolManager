"""Local catalog cache (see docs/DESIGN.md §7).

Startup shows the cached catalog immediately; sync() refreshes it in the
background and only downloads when the remote revision changed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .manifest import LoadResult, load_catalog
from .repository import Repository

REVISION_FILE = "REVISION"


class CatalogCache:
    def __init__(self, repo: Repository, cache_dir: Path) -> None:
        self.repo = repo
        self.cache_dir = cache_dir
        self.current_dir = cache_dir / "current"

    @property
    def revision(self) -> str | None:
        path = self.cache_dir / REVISION_FILE
        return path.read_text(encoding="utf-8").strip() if path.is_file() else None

    def load_cached(self) -> LoadResult | None:
        if self.revision is None or not self.current_dir.is_dir():
            return None
        return load_catalog(self.current_dir)

    def sync(self) -> tuple[LoadResult, bool]:
        """Bring the cache up to date. Returns (result, changed)."""
        remote = self.repo.latest_revision()
        if remote == self.revision and self.current_dir.is_dir():
            return load_catalog(self.current_dir), False

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        staging = self.cache_dir / "staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        self.repo.fetch_catalog(remote, staging)
        # Validate before swapping so a broken fetch never replaces a good cache.
        result = load_catalog(staging)

        old = self.cache_dir / "old"
        shutil.rmtree(old, ignore_errors=True)
        if self.current_dir.exists():
            self.current_dir.rename(old)
        staging.rename(self.current_dir)
        shutil.rmtree(old, ignore_errors=True)
        (self.cache_dir / REVISION_FILE).write_text(remote, encoding="utf-8")
        return result, True
