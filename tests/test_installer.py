from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from packaging.version import Version

from toolmanager.backends.local import LocalRepository
from toolmanager.core.installer import (
    STATE_FILE,
    TRASH,
    Installer,
    InstallError,
    UpdateState,
)
from toolmanager.core.manifest import load_catalog
from toolmanager.core.models import Catalog
from toolmanager.i18n import set_language


class FakeShortcuts:
    def __init__(self) -> None:
        self.created: list[tuple[str, Path]] = []
        self.removed: list[str] = []

    def create(self, name: str, target: Path) -> None:
        self.created.append((name, target))

    def remove(self, name: str) -> None:
        self.removed.append(name)


@pytest.fixture(autouse=True)
def english() -> None:
    set_language("en")


@pytest.fixture
def env(catalog_dir: Path, tmp_path: Path):
    repo = LocalRepository(catalog_dir)
    shortcuts = FakeShortcuts()
    installer = Installer(
        repo,
        root=tmp_path / "tools",
        downloads=tmp_path / "downloads",
        revision=repo.latest_revision,
        client_version=Version("0.1.0"),
        shortcuts=shortcuts,
    )
    return installer, shortcuts, catalog_dir


def catalog(catalog_dir: Path) -> Catalog:
    return load_catalog(catalog_dir).catalog


def add_release(catalog_dir: Path, version: str, *, entry: str = "RegisterViewer.exe",
                extra: str = "", files: dict[str, str] | None = None) -> None:
    release_dir = catalog_dir / "tools" / "register-viewer" / "releases" / version
    release_dir.mkdir()
    (release_dir / "release.toml").write_text(
        f'version = "{version}"\ndate = 2026-10-30\nentry = "{entry}"\n{extra}', "utf-8"
    )
    files = files if files is not None else {"RegisterViewer.exe": "exe"}
    with zipfile.ZipFile(release_dir / f"register-viewer-{version}.zip", "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def test_install_latest(env) -> None:
    installer, shortcuts, catalog_dir = env
    tool = catalog(catalog_dir).tool("register-viewer")
    assert installer.status(tool).state is UpdateState.NOT_INSTALLED

    progress: list[tuple[int, int | None]] = []
    inst = installer.install(tool, progress=lambda d, t: progress.append((d, t)))

    assert inst.current == Version("1.1.0") and inst.previous is None
    assert installer.entry_path(inst).read_text().startswith("placeholder")
    assert installer.status(tool).state is UpdateState.UP_TO_DATE
    assert progress and progress[-1][0] == progress[-1][1]
    assert shortcuts.created == [("Register Viewer", installer.entry_path(inst))]
    assert not list((installer.root / "register-viewer").glob(".staging-*"))


def test_update_keeps_previous_and_rollback(env) -> None:
    installer, shortcuts, catalog_dir = env
    tool = catalog(catalog_dir).tool("register-viewer")
    old = next(r for r in tool.releases if r.version == Version("1.0.0"))
    installer.install(tool, old)

    status = installer.status(tool)
    assert status.state is UpdateState.UPDATE_AVAILABLE
    assert status.important is True  # 1.1.0 is marked important

    inst = installer.install(tool)
    assert (inst.current, inst.previous) == (Version("1.1.0"), Version("1.0.0"))

    inst = installer.rollback("register-viewer")
    assert (inst.current, inst.previous) == (Version("1.0.0"), Version("1.1.0"))
    assert installer.entry_path(inst).read_text() == "placeholder for register-viewer 1.0.0\n"
    assert shortcuts.created[-1][1] == installer.entry_path(inst)

    inst = installer.rollback("register-viewer")
    assert inst.current == Version("1.1.0")


def test_only_current_and_previous_are_kept(env) -> None:
    installer, _, catalog_dir = env
    add_release(catalog_dir, "1.2.0")
    tool = catalog(catalog_dir).tool("register-viewer")
    for version in ("1.0.0", "1.1.0", "1.2.0"):
        installer.install(tool, next(r for r in tool.releases if str(r.version) == version))

    tool_dir = installer.root / "register-viewer"
    versions = sorted(p.name for p in tool_dir.iterdir() if p.is_dir() and p.name[0] != ".")
    assert versions == ["1.1.0", "1.2.0"]
    assert set(installer.installed("register-viewer").entries) == {"1.1.0", "1.2.0"}


def test_reinstalling_current_keeps_previous(env) -> None:
    installer, _, catalog_dir = env
    tool = catalog(catalog_dir).tool("register-viewer")
    installer.install(tool, tool.releases[1])
    installer.install(tool)
    inst = installer.install(tool)
    assert inst.previous == Version("1.0.0")


def test_uninstall(env) -> None:
    installer, shortcuts, catalog_dir = env
    tool = catalog(catalog_dir).tool("register-viewer")
    installer.install(tool)
    installer.uninstall("register-viewer")
    assert installer.installed("register-viewer") is None
    assert not (installer.root / "register-viewer").exists()
    assert shortcuts.removed == ["Register Viewer"]
    assert installer.list_installed() == []


def test_zip_with_single_root_folder(env) -> None:
    installer, _, catalog_dir = env
    add_release(catalog_dir, "1.2.0", files={"RegisterViewer/RegisterViewer.exe": "exe",
                                             "RegisterViewer/lib/a.dll": "dll"})
    tool = catalog(catalog_dir).tool("register-viewer")
    inst = installer.install(tool)
    assert installer.entry_path(inst).read_text() == "exe"


def test_missing_entry_fails_cleanly(env) -> None:
    installer, _, catalog_dir = env
    add_release(catalog_dir, "1.2.0", files={"Other.exe": "x"})
    tool = catalog(catalog_dir).tool("register-viewer")
    with pytest.raises(InstallError, match="entry file"):
        installer.install(tool)
    assert installer.installed("register-viewer") is None
    assert not list((installer.root / "register-viewer").glob(".staging-*"))


def test_not_a_zip(env) -> None:
    installer, _, catalog_dir = env
    add_release(catalog_dir, "1.2.0")
    (catalog_dir / "tools/register-viewer/releases/1.2.0/register-viewer-1.2.0.zip").write_text("x")
    tool = catalog(catalog_dir).tool("register-viewer")
    with pytest.raises(InstallError, match="not a valid zip"):
        installer.install(tool)


def test_requires_newer_client(env) -> None:
    installer, _, catalog_dir = env
    add_release(catalog_dir, "1.2.0", extra='min_client_version = "9.0.0"\n')
    tool = catalog(catalog_dir).tool("register-viewer")
    with pytest.raises(InstallError, match="Requires ToolManager 9.0.0"):
        installer.install(tool)


def test_installer_type_cannot_be_installed(env) -> None:
    installer, _, catalog_dir = env
    path = catalog_dir / "tools" / "register-viewer" / "tool.toml"
    path.write_text(path.read_text("utf-8").replace('type = "exe"', 'type = "installer"'), "utf-8")
    tool = catalog(catalog_dir).tool("register-viewer")
    with pytest.raises(InstallError, match="Installer-type"):
        installer.install(tool)


def test_python_tool_installs_but_cannot_launch_yet(env) -> None:
    installer, shortcuts, catalog_dir = env
    tool = catalog(catalog_dir).tool("csv-plotter")
    installer.install(tool)
    assert shortcuts.created == []  # shortcuts are for exe tools only
    with pytest.raises(InstallError, match="not supported yet"):
        installer.launch("csv-plotter")


def test_launch_not_installed(env) -> None:
    installer, _, _ = env
    with pytest.raises(InstallError, match="not installed"):
        installer.launch("register-viewer")


def test_auto_update_candidates(env) -> None:
    installer, _, catalog_dir = env
    cat = catalog(catalog_dir)
    tool = cat.tool("register-viewer")
    installer.install(tool, tool.releases[1])
    assert installer.auto_update_candidates(cat.tools) == []
    installer.set_auto_update("register-viewer", True)
    assert [t.id for t in installer.auto_update_candidates(cat.tools)] == ["register-viewer"]


def test_corrupt_state_is_ignored(env) -> None:
    installer, _, catalog_dir = env
    tool = catalog(catalog_dir).tool("register-viewer")
    installer.install(tool)
    (installer.root / "register-viewer" / STATE_FILE).write_text("current = ", "utf-8")
    assert installer.installed("register-viewer") is None


def test_cleanup_removes_leftovers(env) -> None:
    installer, _, _ = env
    (installer.root / TRASH / "x").mkdir(parents=True)
    (installer.root / "register-viewer" / ".staging-register-viewer-1.0.0").mkdir(parents=True)
    installer.cleanup()
    assert not (installer.root / TRASH).exists()
    assert not list(installer.root.glob("*/.staging-*"))
