from __future__ import annotations

from pathlib import Path

import pytest
from packaging.version import Version

from toolmanager.core.manifest import ManifestError, load_catalog
from toolmanager.core.models import ArgType, ToolType


def test_load_example_catalog(catalog_dir: Path) -> None:
    result = load_catalog(catalog_dir)
    assert result.problems == ()
    catalog = result.catalog
    assert [c.id for c in catalog.categories][:2] == ["debug", "flash"]
    assert {t.id for t in catalog.tools} == {"csv-plotter", "register-viewer"}

    viewer = catalog.tool("register-viewer")
    assert viewer.type is ToolType.EXE
    assert [r.version for r in viewer.releases] == [Version("1.1.0"), Version("1.0.0")]
    assert viewer.latest.important is True
    assert viewer.latest.artifact == (
        "tools/register-viewer/releases/1.1.0/register-viewer-1.1.0.zip"
    )
    assert "Register Viewer" in viewer.readme

    plotter = catalog.tool("csv-plotter")
    assert plotter.latest.args[0].type is ArgType.FILE
    assert plotter.latest.args[1].default == 150


def test_broken_tool_is_skipped_not_fatal(catalog_dir: Path) -> None:
    (catalog_dir / "tools" / "csv-plotter" / "tool.toml").write_text(
        'id = "csv-plotter"\nname = "x"\nsummary = "x"\ncategory = "nope"\n'
        'owners = ["a"]\ntype = "python"\n',
        encoding="utf-8",
    )
    result = load_catalog(catalog_dir)
    assert [t.id for t in result.catalog.tools] == ["register-viewer"]
    assert len(result.problems) == 1
    assert "unknown category" in str(result.problems[0])


@pytest.mark.parametrize(
    ("folder", "content", "message"),
    [
        ("1.2.0", 'version = "1.2"\ndate = 2026-01-01\nentry = "a"\n', "MAJOR.MINOR.PATCH"),
        ("1.2.0", 'version = "1.3.0"\ndate = 2026-01-01\nentry = "a"\n', "folder name"),
        ("1.2.0", 'version = "1.2.0"\ndate = 2026-01-01\n', "entry"),
        ("1.2.0", 'version = "1.2.0"\ndate = "yesterday"\nentry = "a"\n', "date"),
    ],
)
def test_invalid_release(catalog_dir: Path, folder: str, content: str, message: str) -> None:
    release_dir = catalog_dir / "tools" / "register-viewer" / "releases" / folder
    release_dir.mkdir()
    (release_dir / "release.toml").write_text(content, encoding="utf-8")
    result = load_catalog(catalog_dir)
    assert result.catalog.tool("register-viewer") is None
    assert message in str(result.problems[0])


def test_tool_id_must_match_folder(catalog_dir: Path) -> None:
    path = catalog_dir / "tools" / "csv-plotter" / "tool.toml"
    path.write_text(path.read_text(encoding="utf-8").replace('"csv-plotter"', '"other"'), "utf-8")
    result = load_catalog(catalog_dir)
    assert "folder name" in str(result.problems[0])


def test_invalid_catalog_file_is_fatal(catalog_dir: Path) -> None:
    (catalog_dir / "catalog.toml").write_text("schema_version = 1\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="categories"):
        load_catalog(catalog_dir)
