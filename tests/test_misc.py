from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from toolmanager.core.archive import UnsafeArchiveError, flatten_single_root, safe_extract
from toolmanager.core.lfs import parse_pointer
from toolmanager.core.readme import rewrite_relative_images
from toolmanager.core.settings import Settings
from toolmanager.i18n import _, set_language


def test_parse_pointer() -> None:
    data = b"version https://git-lfs.github.com/spec/v1\noid sha256:ab12\nsize 42\n"
    pointer = parse_pointer(data)
    assert pointer.oid == "ab12" and pointer.size == 42
    assert parse_pointer(b"PK\x03\x04 a real zip") is None


def test_rewrite_relative_images() -> None:
    md = "![a](assets/a.png) ![b](./b.png) ![c](https://x/c.png) ![d](/abs.png)"
    assert rewrite_relative_images(md, "/catalog/tools/t/") == (
        "![a](/catalog/tools/t/assets/a.png) ![b](/catalog/tools/t/b.png) "
        "![c](https://x/c.png) ![d](/abs.png)"
    )


def test_i18n_fallback_and_format() -> None:
    set_language("zh_TW")
    assert _("All tools") == "所有工具"
    assert _("Sync failed: {error}", error="x") == "同步失敗：x"
    assert _("untranslated text") == "untranslated text"
    set_language("en")
    assert _("All tools") == "All tools"


def test_settings_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    assert Settings.load(path) == Settings()
    s = Settings(backend="bitbucket", pypi_index_url="https://pypi.example/simple")
    s.bitbucket.project = "TM"
    s.save(path)
    assert Settings.load(path) == s


def test_safe_extract_rejects_zip_slip(tmp_path: Path) -> None:
    zip_path = tmp_path / "evil.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "x")
    zip_path.write_bytes(buf.getvalue())
    with pytest.raises(UnsafeArchiveError, match="unsafe"):
        safe_extract(zip_path, tmp_path / "out")


def test_flatten_single_root(tmp_path: Path) -> None:
    (tmp_path / "MyTool" / "bin").mkdir(parents=True)
    (tmp_path / "MyTool" / "app.exe").write_text("x")
    flatten_single_root(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["app.exe", "bin"]

    flatten_single_root(tmp_path)  # two entries now: no-op
    assert (tmp_path / "app.exe").exists()
