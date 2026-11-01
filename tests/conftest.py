from __future__ import annotations

import shutil
from pathlib import Path

import pytest

EXAMPLE_CATALOG = Path(__file__).resolve().parents[1] / "examples" / "catalog"


@pytest.fixture
def catalog_dir(tmp_path: Path) -> Path:
    """A writable copy of the example catalog."""
    dest = tmp_path / "catalog"
    shutil.copytree(EXAMPLE_CATALOG, dest)
    return dest
