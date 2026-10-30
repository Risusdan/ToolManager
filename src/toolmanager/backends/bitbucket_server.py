"""Repository backed by a self-hosted Bitbucket Server / Data Center (see docs/DESIGN.md §7).

No git client is needed on the user's machine:
- latest revision: REST commits API
- catalog: REST archive API (zip of the branch at a commit)
- artifacts: raw file -> if it is an LFS pointer, the Git LFS batch API

Which of these the target server actually supports is what the P0 spike verifies
(scripts/p0_check_bitbucket.py).
"""

from __future__ import annotations

import functools
import hashlib
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

import httpx

from ..core.lfs import parse_pointer
from ..core.repository import Repository, RepositoryError

CHUNK = 1024 * 256


def _network_errors(fn):
    """Surface transport failures (DNS, proxy, timeout) as RepositoryError."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except httpx.HTTPError as e:
            raise RepositoryError(f"network error: {e}") from e

    return wrapper


def safe_extract(zip_path: Path, dest: Path) -> None:
    """Extract zip_path into dest, refusing entries that escape dest (zip slip)."""
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(dest_resolved):
                raise RepositoryError(f"unsafe path in archive: {info.filename}")
        zf.extractall(dest)


class BitbucketServerRepository(Repository):
    def __init__(
        self,
        base_url: str,
        project: str,
        repo: str,
        token: str,
        branch: str = "main",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.project = project
        self.repo = repo
        self.branch = branch
        self.token = token
        self.username: str | None = None
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=True)
        self.rest = f"{self.base_url}/rest/api/1.0/projects/{project}/repos/{repo}"

    # -- helpers -----------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _check(self, resp: httpx.Response) -> httpx.Response:
        if resp.status_code == 401:
            raise RepositoryError("authentication failed: token invalid or expired")
        if resp.status_code == 403:
            raise RepositoryError("access denied: token lacks read permission on the repo")
        if resp.status_code == 404:
            raise RepositoryError(f"not found: {resp.request.url}")
        if resp.is_error:
            raise RepositoryError(f"HTTP {resp.status_code} from {resp.request.url}")
        # Bitbucket reports the authenticated user in this header; LFS needs it for basic auth.
        self.username = resp.headers.get("X-AUSERNAME", self.username)
        return resp

    def _stream_to(self, method: str, url: str, dest: Path, **kwargs) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.client.stream(method, url, **kwargs) as resp:
            self._check(resp)
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes(CHUNK):
                    f.write(chunk)

    # -- Repository ----------------------------------------------------------

    @_network_errors
    def latest_revision(self) -> str:
        resp = self._check(
            self.client.get(
                f"{self.rest}/commits",
                params={"until": f"refs/heads/{self.branch}", "limit": 1},
                headers=self._auth_headers(),
            )
        )
        values = resp.json().get("values", [])
        if not values:
            raise RepositoryError(f"branch '{self.branch}' has no commits")
        return values[0]["id"]

    @_network_errors
    def fetch_catalog(self, revision: str, dest: Path) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "catalog.zip"
            self._stream_to(
                "GET",
                f"{self.rest}/archive",
                archive,
                params={"at": revision, "format": "zip"},
                headers=self._auth_headers(),
            )
            safe_extract(archive, dest)

    @_network_errors
    def download_artifact(self, revision: str, rel_path: str, dest: Path) -> Path:
        raw_url = (
            f"{self.base_url}/projects/{self.project}/repos/{self.repo}/raw/"
            f"{quote(rel_path)}"
        )
        self._stream_to("GET", raw_url, dest, params={"at": revision}, headers=self._auth_headers())
        with dest.open("rb") as f:
            pointer = parse_pointer(f.read(1024))
        if pointer is None:
            return dest  # server already served the real content
        self._lfs_download(pointer.oid, pointer.size, dest)
        return dest

    # -- Git LFS ---------------------------------------------------------------

    def _lfs_download(self, oid: str, size: int, dest: Path) -> None:
        if self.username is None:
            self.latest_revision()  # populates username from X-AUSERNAME
        auth = httpx.BasicAuth(self.username or "", self.token)
        git_url = f"{self.base_url}/scm/{self.project.lower()}/{self.repo}.git"
        batch_url = f"{git_url}/info/lfs/objects/batch"
        resp = self._check(
            self.client.post(
                batch_url,
                json={
                    "operation": "download",
                    "transfers": ["basic"],
                    "objects": [{"oid": oid, "size": size}],
                },
                headers={
                    "Accept": "application/vnd.git-lfs+json",
                    "Content-Type": "application/vnd.git-lfs+json",
                },
                auth=auth,
            )
        )
        obj = resp.json()["objects"][0]
        if "error" in obj:
            raise RepositoryError(f"LFS error for {oid}: {obj['error'].get('message')}")
        action = obj["actions"]["download"]

        h = hashlib.sha256()
        written = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.client.stream(
            "GET", action["href"], headers=action.get("header", {}), auth=auth
        ) as dl:
            self._check(dl)
            with dest.open("wb") as f:
                for chunk in dl.iter_bytes(CHUNK):
                    h.update(chunk)
                    written += len(chunk)
                    f.write(chunk)
        if written != size or h.hexdigest() != oid:
            dest.unlink(missing_ok=True)
            raise RepositoryError(f"LFS download corrupted for {oid}")
