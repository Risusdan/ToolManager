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
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from ..core.archive import UnsafeArchiveError, safe_extract
from ..core.lfs import parse_pointer
from ..core.publish import MergeBlocked, Publisher
from ..core.repository import Progress, Repository, RepositoryError

CHUNK = 1024 * 256


def _network_errors(fn):
    """Surface transport failures (DNS, proxy, timeout) as RepositoryError."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except httpx.HTTPError as e:
            raise RepositoryError(f"network error: {e}", kind="network") from e

    return wrapper


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
            raise RepositoryError("authentication failed: token invalid or expired", kind="auth")
        if resp.status_code == 403:
            raise RepositoryError(
                "access denied: token lacks read permission on the repo", kind="forbidden"
            )
        if resp.status_code == 404:
            raise RepositoryError(f"not found: {resp.request.url}", kind="not_found")
        if resp.is_error:
            raise RepositoryError(f"HTTP {resp.status_code} from {resp.request.url}")
        # Bitbucket reports the authenticated user in this header; LFS needs it for basic auth.
        self.username = resp.headers.get("X-AUSERNAME", self.username)
        return resp

    def _stream_to(
        self, method: str, url: str, dest: Path, progress: Progress | None = None, **kwargs
    ) -> str:
        """Stream a response body to dest; returns its sha256 hex digest."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        with self.client.stream(method, url, **kwargs) as resp:
            self._check(resp)
            length = resp.headers.get("Content-Length")
            total = int(length) if length and length.isdigit() else None
            done = 0
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes(CHUNK):
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        return h.hexdigest()

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
            try:
                safe_extract(archive, dest)
            except UnsafeArchiveError as e:
                raise RepositoryError(str(e)) from e

    @_network_errors
    def download_artifact(
        self, revision: str, rel_path: str, dest: Path, progress: Progress | None = None
    ) -> Path:
        raw_url = (
            f"{self.base_url}/projects/{self.project}/repos/{self.repo}/raw/"
            f"{quote(rel_path)}"
        )
        self._stream_to(
            "GET", raw_url, dest, progress, params={"at": revision}, headers=self._auth_headers()
        )
        with dest.open("rb") as f:
            pointer = parse_pointer(f.read(1024))
        if pointer is None:
            return dest  # server already served the real content
        self._lfs_download(pointer.oid, pointer.size, dest, progress)
        return dest

    # -- Git LFS ---------------------------------------------------------------

    def _lfs_download(
        self, oid: str, size: int, dest: Path, progress: Progress | None = None
    ) -> None:
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
        href = action["href"]
        # Only send our credentials back to the Bitbucket host itself.
        same_host = urlsplit(href).netloc == urlsplit(self.base_url).netloc

        def report(done: int, _total: int | None) -> None:
            if progress:
                progress(done, size)

        digest = self._stream_to(
            "GET",
            href,
            dest,
            report,
            headers=action.get("header", {}),
            auth=auth if same_host else None,
        )
        if dest.stat().st_size != size or digest != oid:
            dest.unlink(missing_ok=True)
            raise RepositoryError(f"LFS download corrupted for {oid}")


class BitbucketPublisher(Publisher):
    """Publishing through the Bitbucket Server / Data Center REST API (docs/DESIGN.md §8.1).

    Endpoints: Git LFS batch (upload), branches, browse (one commit per file), pull-requests
    and merge. Like the read side, which of these co-git allows is what P0 verifies.
    """

    def __init__(self, repo: BitbucketServerRepository) -> None:
        self.repo = repo
        self._heads: dict[str, str] = {}  # branch -> latest commit we know of

    def _send(self, method: str, url: str, ok_codes: tuple[int, ...] = (), **kwargs):
        resp = self.repo.client.request(method, url, **kwargs)
        if resp.status_code in ok_codes:
            return resp
        if resp.status_code == 403:
            raise RepositoryError("access denied: your account cannot write to the catalog repo",
                                  kind="forbidden")
        return self.repo._check(resp)

    @staticmethod
    def _message(resp: httpx.Response) -> str:
        try:
            errors = resp.json().get("errors", [])
        except ValueError:
            return resp.text[:300]
        parts = []
        for error in errors:
            parts.append(error.get("message", ""))
            parts += [v.get("summaryMessage", "") for v in error.get("vetoes", [])]
        return "; ".join(p for p in parts if p) or f"HTTP {resp.status_code}"

    def known_user(self) -> str:
        return self.repo.username or ""  # set by every API response (X-AUSERNAME)

    @_network_errors
    def current_user(self) -> str:
        if self.repo.username is None:
            self.repo.latest_revision()  # Bitbucket names the user in X-AUSERNAME
        return self.repo.username or ""

    @_network_errors
    def upload_lfs(self, path: Path, oid: str, size: int) -> None:
        auth = httpx.BasicAuth(self.current_user(), self.repo.token)
        git_url = (f"{self.repo.base_url}/scm/{self.repo.project.lower()}/{self.repo.repo}.git")
        headers = {"Accept": "application/vnd.git-lfs+json",
                   "Content-Type": "application/vnd.git-lfs+json"}
        resp = self._send("POST", f"{git_url}/info/lfs/objects/batch", auth=auth,
                          headers=headers, json={"operation": "upload", "transfers": ["basic"],
                                                 "objects": [{"oid": oid, "size": size}]})
        obj = resp.json()["objects"][0]
        if "error" in obj:
            raise RepositoryError(f"LFS upload refused: {obj['error'].get('message')}")
        actions = obj.get("actions") or {}
        upload = actions.get("upload")
        if upload is None:
            return  # the server already has this content
        same_host = urlsplit(upload["href"]).netloc == urlsplit(self.repo.base_url).netloc
        with path.open("rb") as f:
            self._send("PUT", upload["href"], content=f,
                       headers={"Content-Type": "application/octet-stream",
                                **upload.get("header", {})},
                       auth=auth if same_host else None, timeout=None)
        verify = actions.get("verify")
        if verify is not None:
            self._send("POST", verify["href"], json={"oid": oid, "size": size},
                       headers={**headers, **verify.get("header", {})},
                       auth=auth if same_host else None)

    @_network_errors
    def create_branch(self, name: str) -> str:
        for attempt in range(1, 20):
            candidate = name if attempt == 1 else f"{name}-{attempt}"
            resp = self._send("POST", f"{self.repo.rest}/branches", ok_codes=(409,),
                              headers=self.repo._auth_headers(),
                              json={"name": candidate,
                                    "startPoint": f"refs/heads/{self.repo.branch}"})
            if resp.status_code == 409:
                continue  # an earlier attempt left this branch behind
            self._heads[candidate] = resp.json().get("latestCommit", "")
            return candidate
        raise RepositoryError(f"could not create a branch named {name}")

    @_network_errors
    def commit_file(self, branch: str, rel_path: str, content: bytes, message: str) -> None:
        url = f"{self.repo.rest}/browse/{quote(rel_path)}"
        form = {"branch": branch, "message": message}
        name = rel_path.rsplit("/", 1)[-1]
        resp = self._send("PUT", url, ok_codes=(409,), headers=self.repo._auth_headers(),
                          data=form, files={"content": (name, content)})
        if resp.status_code == 409:
            # The file exists already (tool.toml, README.md): editing needs the commit the
            # change is based on.
            resp = self._send("PUT", url, ok_codes=(409,), headers=self.repo._auth_headers(),
                              data={**form, "sourceCommitId": self._heads.get(branch, "")},
                              files={"content": (name, content)})
            if resp.status_code == 409:
                raise RepositoryError(f"could not commit {rel_path}: {self._message(resp)}")
        self._heads[branch] = resp.json().get("id", self._heads.get(branch, ""))

    @_network_errors
    def open_pull_request(self, branch: str, title: str, description: str) -> tuple[str, object]:
        ref = {"repository": {"slug": self.repo.repo, "project": {"key": self.repo.project}}}
        resp = self._send("POST", f"{self.repo.rest}/pull-requests",
                          headers=self.repo._auth_headers(),
                          json={"title": title, "description": description,
                                "fromRef": {"id": f"refs/heads/{branch}", **ref},
                                "toRef": {"id": f"refs/heads/{self.repo.branch}", **ref}})
        pr = resp.json()
        links = pr.get("links", {}).get("self", [])
        return (links[0]["href"] if links else ""), (pr["id"], pr.get("version", 0))

    @_network_errors
    def merge_pull_request(self, handle: object) -> None:
        pr_id, version = handle  # type: ignore[misc]
        resp = self._send("POST", f"{self.repo.rest}/pull-requests/{pr_id}/merge",
                          ok_codes=(409,), headers=self.repo._auth_headers(),
                          params={"version": version})
        if resp.status_code == 409:
            raise MergeBlocked(self._message(resp))
