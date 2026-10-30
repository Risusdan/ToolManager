from __future__ import annotations

import os
from pathlib import Path

import pytest

from toolmanager.backends.local import LocalRepository
from toolmanager.core.catalog_cache import CatalogCache
from toolmanager.core.repository import RepositoryError

ARTIFACT = "tools/register-viewer/releases/1.1.0/register-viewer-1.1.0.zip"


def test_sync_downloads_once_then_uses_cache(catalog_dir: Path, tmp_path: Path) -> None:
    cache = CatalogCache(LocalRepository(catalog_dir), tmp_path / "cache")
    assert cache.load_cached() is None

    result, changed = cache.sync()
    assert changed is True
    assert len(result.catalog.tools) == 2
    assert cache.load_cached().catalog == result.catalog

    _, changed = cache.sync()
    assert changed is False


def test_sync_picks_up_changes(catalog_dir: Path, tmp_path: Path) -> None:
    cache = CatalogCache(LocalRepository(catalog_dir), tmp_path / "cache")
    cache.sync()
    tool_toml = catalog_dir / "tools" / "csv-plotter" / "tool.toml"
    tool_toml.write_text(
        tool_toml.read_text(encoding="utf-8").replace("CSV Plotter", "CSV Plotter 2"), "utf-8"
    )
    os.utime(tool_toml, ns=(1, 1))  # make sure the revision changes even on coarse clocks

    result, changed = cache.sync()
    assert changed is True
    assert result.catalog.tool("csv-plotter").name == "CSV Plotter 2"


def test_artifacts_are_not_part_of_catalog(catalog_dir: Path, tmp_path: Path) -> None:
    (catalog_dir / ARTIFACT).write_bytes(b"zip")
    repo = LocalRepository(catalog_dir)
    cache = CatalogCache(repo, tmp_path / "cache")
    cache.sync()
    assert not (cache.current_dir / ARTIFACT).exists()

    out = repo.download_artifact(cache.revision, ARTIFACT, tmp_path / "dl" / "a.zip")
    assert out.read_bytes() == b"zip"


def test_missing_artifact(catalog_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(RepositoryError):
        LocalRepository(catalog_dir).download_artifact("x", ARTIFACT, tmp_path / "a.zip")
