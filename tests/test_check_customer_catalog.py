from __future__ import annotations

import importlib.util
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_customer_catalog.py"
spec = importlib.util.spec_from_file_location("check_customer_catalog", SCRIPT)
checker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker  # dataclasses look the module up while it executes
spec.loader.exec_module(checker)


@pytest.fixture
def customer_catalog(catalog_dir: Path) -> Path:
    """The example catalog turned into a valid customer catalog."""
    path = catalog_dir / "catalog.toml"
    text = path.read_text("utf-8")
    text = text[: text.index("# Problem reports")] if "# Problem reports" in text else text
    path.write_text("python_offline_only = true\n" + text, "utf-8")  # top-level key: first
    zip_path = catalog_dir / "tools/csv-plotter/releases/0.3.0/csv-plotter-0.3.0.zip"
    with zipfile.ZipFile(zip_path, "a") as zf:
        zf.writestr("wheels/tabulate-0.9.0-py3-none-any.whl", "wheel")
    return catalog_dir


def test_valid_customer_catalog_passes(customer_catalog: Path, capsys) -> None:
    assert checker.main([str(customer_catalog)]) == 0
    assert "0 個錯誤" in capsys.readouterr().out


def test_example_internal_catalog_is_rejected(catalog_dir: Path) -> None:
    report = checker.check(catalog_dir, [])
    text = "\n".join(report.errors)
    assert "[jira]" in text  # internal Jira settings
    assert "python_offline_only" in text
    assert "csv-plotter 0.3.0" in text and "wheels" in text  # needs tabulate, no wheels


def test_offline_flag_under_a_table_gets_a_hint(catalog_dir: Path) -> None:
    path = catalog_dir / "catalog.toml"
    path.write_text(path.read_text("utf-8").split("# Problem reports")[0]
                    + "python_offline_only = true\n", "utf-8")  # ends up in the last category
    report = checker.check(catalog_dir, [])
    assert any("[[categories]]" in e for e in report.errors)


def test_internal_keywords_are_found_everywhere(customer_catalog: Path, tmp_path: Path) -> None:
    readme = customer_catalog / "tools/register-viewer/README.md"
    readme.write_text(readme.read_text("utf-8") + "\n詳見 http://wiki.corp.local/rv\n", "utf-8")
    zip_path = customer_catalog / "tools/register-viewer/releases/1.1.0/register-viewer-1.1.0.zip"
    with zipfile.ZipFile(zip_path, "a") as zf:
        zf.writestr("docs/ProjectFalcon-notes.txt", "x")
    deny = tmp_path / "deny.txt"
    deny.write_text("# internal hosts\n\\.corp\\.local\nproject\\s*falcon\n", "utf-8")

    tool_toml = customer_catalog / "tools/csv-plotter/tool.toml"
    tool_toml.write_text(tool_toml.read_text("utf-8") + 'issue_url = "http://jira.corp.local"\n',
                         "utf-8")

    report = checker.check(customer_catalog, checker.load_patterns(deny))
    assert any("register-viewer README" in e and ".corp.local" in e for e in report.errors)
    assert any("csv-plotter tool.toml" in e for e in report.errors)  # any line of it
    assert any("套件內檔名" in e and "ProjectFalcon" in e for e in report.errors)


def test_lfs_pointer_is_a_warning_not_a_pass(customer_catalog: Path) -> None:
    zip_path = customer_catalog / "tools/register-viewer/releases/1.0.0/register-viewer-1.0.0.zip"
    zip_path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:ab\nsize 3\n",
                        "utf-8")
    report = checker.check(customer_catalog, [])
    assert any("git lfs pull" in w for w in report.warnings)


def test_missing_artifact_is_an_error(customer_catalog: Path) -> None:
    shutil.rmtree(customer_catalog / "tools/register-viewer/releases/1.0.0")
    (customer_catalog / "tools/register-viewer/releases/1.0.0").mkdir()
    src = customer_catalog / "tools/register-viewer/releases/1.1.0/release.toml"
    (customer_catalog / "tools/register-viewer/releases/1.0.0/release.toml").write_text(
        src.read_text("utf-8").replace('"1.1.0"', '"1.0.0"'), "utf-8")
    report = checker.check(customer_catalog, [])
    assert any("找不到" in e for e in report.errors)
