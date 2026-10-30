"""BitbucketServerRepository against a mocked HTTP server (no network)."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from toolmanager.backends.bitbucket_server import BitbucketServerRepository
from toolmanager.core.manifest import load_catalog
from toolmanager.core.repository import RepositoryError

BASE = "https://bb.example.com"
REST = f"{BASE}/rest/api/1.0/projects/TM/repos/catalog"
ARTIFACT = "tools/register-viewer/releases/1.1.0/register-viewer-1.1.0.zip"
PAYLOAD = b"real zip bytes" * 100
OID = hashlib.sha256(PAYLOAD).hexdigest()
POINTER = (
    f"version https://git-lfs.github.com/spec/v1\noid sha256:{OID}\nsize {len(PAYLOAD)}\n"
).encode()


def _zip_dir(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(root).as_posix())
    return buf.getvalue()


def make_repo(catalog_dir: Path, *, token: str = "good", payload: bytes = PAYLOAD):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        auth = request.headers.get("Authorization", "")
        if auth not in ("Bearer good", "Basic " + _basic("alice", "good")):
            return httpx.Response(401)
        headers = {"X-AUSERNAME": "alice"}
        url = str(request.url).split("?")[0]
        if url == f"{REST}/commits":
            return httpx.Response(200, json={"values": [{"id": "abc123"}]}, headers=headers)
        if url == f"{REST}/archive":
            assert request.url.params["at"] == "abc123"
            return httpx.Response(200, content=_zip_dir(catalog_dir), headers=headers)
        if url == f"{BASE}/projects/TM/repos/catalog/raw/{ARTIFACT}":
            return httpx.Response(200, content=POINTER, headers=headers)
        if url == f"{BASE}/scm/tm/catalog.git/info/lfs/objects/batch":
            body = json.loads(request.content)
            assert body["objects"][0]["oid"] == OID
            return httpx.Response(
                200,
                json={"objects": [{"oid": OID, "actions": {"download": {
                    "href": f"{BASE}/lfs/{OID}", "header": {}}}}]},
            )
        if url == f"{BASE}/lfs/{OID}":
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return BitbucketServerRepository(BASE, "TM", "catalog", token, client=client), calls


def _basic(user: str, password: str) -> str:
    import base64

    return base64.b64encode(f"{user}:{password}".encode()).decode()


def test_latest_revision_and_fetch_catalog(catalog_dir: Path, tmp_path: Path) -> None:
    repo, _ = make_repo(catalog_dir)
    rev = repo.latest_revision()
    assert rev == "abc123"
    assert repo.username == "alice"

    dest = tmp_path / "out"
    dest.mkdir()
    repo.fetch_catalog(rev, dest)
    assert len(load_catalog(dest).catalog.tools) == 2


def test_download_artifact_via_lfs(catalog_dir: Path, tmp_path: Path) -> None:
    repo, calls = make_repo(catalog_dir)
    out = repo.download_artifact("abc123", ARTIFACT, tmp_path / "a.zip")
    assert out.read_bytes() == PAYLOAD
    assert any("info/lfs/objects/batch" in c for c in calls)


def test_corrupted_lfs_download_is_rejected(catalog_dir: Path, tmp_path: Path) -> None:
    repo, _ = make_repo(catalog_dir, payload=b"tampered")
    with pytest.raises(RepositoryError, match="corrupted"):
        repo.download_artifact("abc123", ARTIFACT, tmp_path / "a.zip")
    assert not (tmp_path / "a.zip").exists()


def test_bad_token(catalog_dir: Path) -> None:
    repo, _ = make_repo(catalog_dir, token="bad")
    with pytest.raises(RepositoryError, match="authentication failed"):
        repo.latest_revision()


def test_network_error_becomes_repository_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    repo = BitbucketServerRepository(BASE, "TM", "catalog", "good", client=client)
    with pytest.raises(RepositoryError, match="network error"):
        repo.latest_revision()
