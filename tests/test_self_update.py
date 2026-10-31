from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from packaging.version import Version

from toolmanager.backends.local import LocalRepository
from toolmanager.core.manifest import load_catalog
from toolmanager.core.self_update import (
    APP_EXE,
    DISABLED_MARKER,
    SELF_TOOL_ID,
    SelfInstall,
    SelfUpdateError,
    SelfUpdater,
    detect,
)
from toolmanager.core.trash import COMPLETE_MARKER, Trash
from toolmanager.i18n import set_language


@pytest.fixture(autouse=True)
def english() -> None:
    set_language("en")


def add_self_release(catalog_dir: Path, version: str, files: dict[str, str] | None = None) -> None:
    tool_dir = catalog_dir / "tools" / SELF_TOOL_ID
    if not (tool_dir / "tool.toml").exists():
        tool_dir.mkdir(parents=True)
        (tool_dir / "tool.toml").write_text(
            f'id = "{SELF_TOOL_ID}"\nname = "ToolManager"\nsummary = "x"\ncategory = "misc"\n'
            'owners = ["admin-a"]\ntype = "exe"\n', "utf-8")
    rel = tool_dir / "releases" / version
    rel.mkdir(parents=True)
    (rel / "release.toml").write_text(
        f'version = "{version}"\ndate = 2026-10-31\nentry = "{APP_EXE}"\n', "utf-8")
    files = files if files is not None else {f"ToolManagerApp/{APP_EXE}": "exe",
                                             "ToolManagerApp/_internal/lib.dll": "dll"}
    with zipfile.ZipFile(rel / f"{SELF_TOOL_ID}-{version}.zip", "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def make_app_dir(root: Path, version: str, complete: bool = True) -> Path:
    path = root / f"app-{version}"
    path.mkdir(parents=True)
    (path / APP_EXE).write_text("exe", "utf-8")
    if complete:
        (path / COMPLETE_MARKER).touch()
    return path


@pytest.fixture
def setup(catalog_dir: Path, tmp_path: Path):
    root = tmp_path / "install"
    root.mkdir()
    make_app_dir(root, "0.1.0")
    repo = LocalRepository(catalog_dir)
    install = SelfInstall(root=root, launcher=root / "ToolManager.exe", current=Version("0.1.0"))

    def updater(install: SelfInstall = install) -> SelfUpdater:
        return SelfUpdater(install, repo, repo.latest_revision, Trash(tmp_path / "trash", [root]))

    return updater, catalog_dir, root


def self_tool(catalog_dir: Path):
    return load_catalog(catalog_dir).catalog.tool(SELF_TOOL_ID)


def test_detect() -> None:
    assert detect(Version("1.0.0"), {}) is None
    inst = detect(Version("1.0.0"), {"TOOLMANAGER_LAUNCHER": "/opt/tm/ToolManager.exe",
                                     "TOOLMANAGER_FAILED_VERSION": "1.1.0"})
    assert inst.root == Path("/opt/tm") and inst.failed_version == "1.1.0"


def test_download_prepares_complete_app_folder(setup) -> None:
    updater, catalog_dir, root = setup
    add_self_release(catalog_dir, "0.2.0")
    up = updater()
    release = up.available(self_tool(catalog_dir))
    assert release.version == Version("0.2.0")

    target = up.download(release)
    assert target == root / "app-0.2.0"
    assert (target / APP_EXE).is_file() and (target / "_internal" / "lib.dll").is_file()
    assert (target / COMPLETE_MARKER).is_file()
    assert up.pending() == Version("0.2.0")
    assert up.available(self_tool(catalog_dir)) is None  # already prepared
    assert not list(root.glob(".staging-*")) and not list(root.glob(".download-*"))


def test_nothing_newer_means_nothing_to_do(setup) -> None:
    updater, catalog_dir, _ = setup
    assert updater().available(None) is None
    add_self_release(catalog_dir, "0.1.0")
    assert updater().available(self_tool(catalog_dir)) is None


def test_broken_package_leaves_nothing_behind(setup) -> None:
    updater, catalog_dir, root = setup
    add_self_release(catalog_dir, "0.2.0", files={"README.txt": "no exe"})
    up = updater()
    with pytest.raises(SelfUpdateError, match="entry file"):
        up.download(up.available(self_tool(catalog_dir)))
    assert not (root / "app-0.2.0").exists()
    assert not list(root.glob(".staging-*"))
    assert up.pending() is None


def test_foreign_folder_is_not_taken_over(setup) -> None:
    updater, catalog_dir, root = setup
    add_self_release(catalog_dir, "0.2.0")
    make_app_dir(root, "0.2.0", complete=False)  # e.g. copied by hand, not ours
    up = updater()
    release = up.available(self_tool(catalog_dir))
    with pytest.raises(SelfUpdateError, match="not created by ToolManager"):
        up.download(release)
    assert (root / "app-0.2.0" / APP_EXE).read_text("utf-8") == "exe"


def test_disable_marks_version_and_stops_redownload(setup) -> None:
    updater, catalog_dir, root = setup
    add_self_release(catalog_dir, "0.2.0")
    up = updater()
    up.download(up.available(self_tool(catalog_dir)))
    up.disable("0.2.0")
    assert (root / "app-0.2.0" / DISABLED_MARKER).exists()
    assert (root / "app-0.2.0" / APP_EXE).is_file()  # nothing deleted
    assert up.pending() is None
    assert up.available(self_tool(catalog_dir)) is None
    with pytest.raises(SelfUpdateError):
        up.disable("0.1.0")  # the running version
    with pytest.raises(SelfUpdateError):
        up.disable("../x")


def test_prune_keeps_current_and_one_fallback(setup, tmp_path: Path) -> None:
    updater, _, root = setup
    for v in ("0.0.1", "0.0.2", "0.0.3"):
        make_app_dir(root, v)
    make_app_dir(root, "0.0.4", complete=False)
    install = SelfInstall(root=root, launcher=root / "ToolManager.exe", current=Version("0.1.0"))
    up = updater(install)
    up.prune()
    remaining = sorted(p.name for p in root.iterdir() if p.name.startswith("app-"))
    assert remaining == ["app-0.0.3", "app-0.0.4", "app-0.1.0"]
    assert sorted(i.label for i in up.trash.items()) == ["ToolManager 0.0.1", "ToolManager 0.0.2"]


def test_no_prune_while_running_as_fallback(setup) -> None:
    updater, _, root = setup
    for v in ("0.0.1", "0.0.2"):
        make_app_dir(root, v)
    install = SelfInstall(root=root, launcher=root / "ToolManager.exe", current=Version("0.1.0"),
                          failed_version="0.2.0")
    updater(install).prune()
    assert (root / "app-0.0.1").is_dir()


def test_failed_version_is_not_offered_again(setup) -> None:
    updater, catalog_dir, root = setup
    add_self_release(catalog_dir, "0.2.0")
    updater().download(updater().available(self_tool(catalog_dir)))
    install = SelfInstall(root=root, launcher=root / "ToolManager.exe", current=Version("0.1.0"),
                          failed_version="0.2.0")
    up = updater(install)
    assert up.pending() is None
    assert up.available(self_tool(catalog_dir)) is None  # already on disk: not re-downloaded
