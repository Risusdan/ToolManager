from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from toolmanager.core.catalog_check import check_catalog

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_catalog.py"
spec = importlib.util.spec_from_file_location("check_catalog", SCRIPT)
script = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = script
spec.loader.exec_module(script)

RV = "tools/register-viewer"


def all_in_lfs(path: str) -> bool:
    return True


@pytest.fixture
def base(catalog_dir: Path, tmp_path: Path) -> Path:
    """The target branch: the catalog before the change."""
    dest = tmp_path / "base"
    shutil.copytree(catalog_dir, dest)
    return dest


def add_release(catalog: Path, version: str, entry: str = "RegisterViewer.exe") -> None:
    folder = catalog / RV / "releases" / version
    folder.mkdir()
    (folder / "release.toml").write_text(
        f'version = "{version}"\ndate = 2026-11-01\nentry = "{entry}"\nchangelog = "x"\n', "utf-8")
    with zipfile.ZipFile(folder / f"register-viewer-{version}.zip", "w") as zf:
        zf.writestr("RegisterViewer/RegisterViewer.exe", "exe")  # zipped with a top folder


def test_example_catalog_passes(catalog_dir: Path) -> None:
    report = check_catalog(catalog_dir, lfs_tracked=all_in_lfs)
    assert report.errors == [] and report.warnings == []


def test_package_problems(catalog_dir: Path) -> None:
    add_release(catalog_dir, "1.2.0", entry="Missing.exe")
    (catalog_dir / RV / "releases/1.0.0/register-viewer-1.0.0.zip").write_bytes(b"not a zip")
    report = check_catalog(catalog_dir, lfs_tracked=lambda p: "1.1.0" not in p)
    text = "\n".join(report.errors)
    assert "找不到 entry「Missing.exe」" in text
    assert "不是有效的 zip" in text
    assert "register-viewer-1.1.0.zip 沒有用 Git LFS 儲存" in text


def test_readme_images(catalog_dir: Path) -> None:
    readme = catalog_dir / RV / "README.md"
    (catalog_dir / RV / "assets/big.png").write_bytes(b"0" * (2 * 1024 * 1024 + 1))
    readme.write_text(readme.read_text("utf-8") + "\n![a](assets/none.png)\n![b](assets/big.png)"
                      "\n![c](../csv-plotter/README.md)\n![d](https://example.com/x.png)\n",
                      "utf-8")
    text = "\n".join(check_catalog(catalog_dir, lfs_tracked=all_in_lfs).errors)
    assert "找不到圖片 assets/none.png" in text
    assert "assets/big.png 為 2.0 MB" in text
    assert "../csv-plotter/README.md 必須放在工具資料夾內" in text
    assert "example.com" not in text


def test_new_version_by_an_owner(catalog_dir: Path, base: Path) -> None:
    add_release(catalog_dir, "1.2.0")
    report = check_catalog(catalog_dir, base, "user-a", all_in_lfs)
    assert report.errors == []
    assert report.notes == ["register-viewer：新版本 1.2.0"]


def test_change_rules(catalog_dir: Path, base: Path) -> None:
    add_release(catalog_dir, "1.0.1")  # older than the latest 1.1.0
    notes = catalog_dir / RV / "releases/1.1.0/release.toml"
    notes.write_text(notes.read_text("utf-8").replace("大檔案", "大型檔案"), "utf-8")
    report = check_catalog(catalog_dir, base, "user-b", all_in_lfs)
    text = "\n".join(report.errors)
    assert "1.0.1：新版本號必須大於目前最新的 1.1.0" in text
    assert "1.1.0：已發布的版本不可修改" in text
    assert "user-b 不在 owners（user-a）" in text
    assert "csv-plotter" not in text  # unchanged tools are not the author's concern


def test_admins_new_tools_and_catalog_changes(catalog_dir: Path, base: Path) -> None:
    shutil.copytree(catalog_dir / RV, catalog_dir / "tools/reg-viewer-2")
    tool_toml = catalog_dir / "tools/reg-viewer-2/tool.toml"
    tool_toml.write_text(tool_toml.read_text("utf-8").replace(
        '"register-viewer"', '"reg-viewer-2"'), "utf-8")
    for zip_path in (catalog_dir / "tools/reg-viewer-2/releases").rglob("*.zip"):
        zip_path.rename(zip_path.with_name(zip_path.name.replace("register-viewer",
                                                                  "reg-viewer-2")))
    (catalog_dir / "catalog.toml").write_text(
        (catalog_dir / "catalog.toml").read_text("utf-8") + "\n# comment\n", "utf-8")
    shutil.rmtree(catalog_dir / "tools/csv-plotter")

    report = check_catalog(catalog_dir, base, "user-a", all_in_lfs)
    assert "reg-viewer-2：新工具，需管理員核准" in report.notes
    assert "catalog.toml 有變更：需由管理員審核" in report.notes
    assert any("user-a 不是管理員" in e for e in report.errors)
    assert any("csv-plotter：整個工具被移除" in w for w in report.warnings)
    assert check_catalog(catalog_dir, base, "admin-a", all_in_lfs).errors == []


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@x",
                    *args], check=True, capture_output=True)


def test_script_against_a_git_branch(catalog_dir: Path, capsys) -> None:
    (catalog_dir / ".gitattributes").write_text(
        "tools/*/releases/*/*.zip filter=lfs diff=lfs merge=lfs -text\n", "utf-8")
    _git(catalog_dir, "init", "-q", "-b", "main")
    _git(catalog_dir, "add", "-A")
    _git(catalog_dir, "commit", "-q", "-m", "base")
    _git(catalog_dir, "checkout", "-q", "-b", "publish")
    add_release(catalog_dir, "1.2.0")
    _git(catalog_dir, "add", "-A")
    _git(catalog_dir, "commit", "-q", "-m", "1.2.0")

    assert script.main([str(catalog_dir), "--base", "main", "--author", "user-a"]) == 0
    out = capsys.readouterr().out
    assert "[說明] register-viewer：新版本 1.2.0" in out and "0 個錯誤" in out
    assert script.main([str(catalog_dir), "--base", "main", "--author", "user-b"]) == 1
