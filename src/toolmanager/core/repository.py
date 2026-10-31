"""Storage abstraction for the catalog (see docs/DESIGN.md §2.1).

The client only ever needs three things from the backing store, so swapping
Bitbucket for a dedicated server later only means adding another implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

# progress(bytes_done, bytes_total_or_None)
Progress = Callable[[int, int | None], None]


class RepositoryError(Exception):
    pass


class Repository(ABC):
    @abstractmethod
    def latest_revision(self) -> str:
        """Cheap call returning an opaque id that changes whenever the catalog changes."""

    @abstractmethod
    def fetch_catalog(self, revision: str, dest: Path) -> None:
        """Write the catalog tree (manifests, READMEs, assets; no large artifacts) into dest."""

    @abstractmethod
    def download_artifact(
        self, revision: str, rel_path: str, dest: Path, progress: Progress | None = None
    ) -> Path:
        """Download a release artifact (e.g. a tool zip) to dest and return its path."""
