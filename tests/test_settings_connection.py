from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from toolmanager.core.connection import check_connection, token_page_url
from toolmanager.core.repository import RepositoryError
from toolmanager.core.settings import BitbucketSettings, Settings, normalize_base_url
from toolmanager.i18n import set_language
from toolmanager.ui.setup_pages import connection_error_message

BB = BitbucketSettings("https://bb.example.com", "TM", "catalog", "main")


def test_defaults_file_is_overridden_per_field(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.toml"
    defaults.write_text(
        'pypi_index_url = "https://pypi.example.com/simple"\n'
        '[bitbucket]\nbase_url = "https://bb.example.com"\nproject = "TM"\nrepo = "catalog"\n',
        "utf-8",
    )
    user = tmp_path / "settings.toml"
    assert Settings.load(user, defaults).bitbucket.project == "TM"
    assert Settings.load(user, defaults).server_configured()

    user.write_text('check_interval_hours = 2\n[bitbucket]\nbranch = "dev"\n', "utf-8")
    s = Settings.load(user, defaults)
    assert (s.bitbucket.base_url, s.bitbucket.branch) == ("https://bb.example.com", "dev")
    assert s.check_interval_hours == 2
    assert s.pypi_index_url == "https://pypi.example.com/simple"


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    user = tmp_path / "settings.toml"
    user.write_text('future_option = 1\n[bitbucket]\nnew_field = "x"\n', "utf-8")
    assert Settings.load(user) == Settings()


def test_save_is_atomic_and_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    s = Settings(bitbucket=BitbucketSettings("https://bb", "P", "r"))
    s.save(path)
    assert Settings.load(path) == s
    assert not path.with_suffix(".tmp").exists()


def test_server_configured() -> None:
    assert not Settings().server_configured()
    assert Settings(bitbucket=BB).server_configured()
    assert Settings(backend="local", local_catalog="/x").server_configured()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://bb.example.com/", "https://bb.example.com"),
        ("  http://host:7990/bitbucket  ", "http://host:7990/bitbucket"),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "bb.example.com", "ftp://x", "https://", "https://x/?a=1"])
def test_normalize_base_url_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_base_url(raw)


def _client(status: int, json: dict | None = None, exc: Exception | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if exc:
            raise exc
        return httpx.Response(status, json=json, headers={"X-AUSERNAME": "alice"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_check_connection_ok() -> None:
    info = check_connection(BB, "t", _client(200, {"values": [{"id": "abc"}]}))
    assert (info.username, info.revision) == ("alice", "abc")


@pytest.mark.parametrize(
    ("status", "kind", "message"),
    [(401, "auth", "過期"), (403, "forbidden", "權限"), (404, "not_found", "Project key")],
)
def test_check_connection_errors(status: int, kind: str, message: str) -> None:
    set_language("zh_TW")
    with pytest.raises(RepositoryError) as e:
        check_connection(BB, "t", _client(status))
    assert e.value.kind == kind
    assert message in connection_error_message(e.value)


def test_check_connection_network_error() -> None:
    set_language("zh_TW")
    request = httpx.Request("GET", "https://bb.example.com")
    with pytest.raises(RepositoryError) as e:
        check_connection(BB, "t", _client(0, exc=httpx.ConnectError("down", request=request)))
    assert e.value.kind == "network"
    assert "VPN" in connection_error_message(e.value)


def test_token_page_url() -> None:
    assert token_page_url("https://bb.example.com/") == (
        "https://bb.example.com/plugins/servlet/access-tokens/manage"
    )


def test_save_writes_only_changes_from_defaults(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.toml"
    server = '[bitbucket]\nbase_url = "https://{}"\nproject = "TM"\nrepo = "c"\n'
    defaults.write_text(server.format("old"), "utf-8")
    user = tmp_path / "settings.toml"
    s = Settings.load(user, defaults)
    s.check_interval_hours = 2
    s.save(user, Settings.load(None, defaults))
    assert user.read_text("utf-8") == "check_interval_hours = 2\n"

    # The department moves the server: users who never edited it follow along.
    defaults.write_text(server.format("new"), "utf-8")
    s = Settings.load(user, defaults)
    assert (s.bitbucket.base_url, s.check_interval_hours) == ("https://new", 2)


def test_defaults_file_sits_next_to_the_launcher(monkeypatch, tmp_path: Path) -> None:
    from toolmanager.core import paths

    monkeypatch.delenv("TOOLMANAGER_DEFAULTS", raising=False)
    monkeypatch.setenv("TOOLMANAGER_LAUNCHER", str(tmp_path / "ToolManager.exe"))
    assert paths.defaults_file() == tmp_path / "defaults.toml"
    monkeypatch.setenv("TOOLMANAGER_DEFAULTS", str(tmp_path / "custom.toml"))
    assert paths.defaults_file() == tmp_path / "custom.toml"
