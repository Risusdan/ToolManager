"""In-memory Bitbucket Server for publish tests (served through httpx.MockTransport).

Behaves like the documented REST API where it matters to the publisher: branch creation
(409 when it exists), browse PUT (409 when a file exists and no sourceCommitId is given),
pull requests, merge (409 with vetoes when blocked) and the Git LFS batch upload.
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

BASE = "https://bb.example.com"
REST = "/rest/api/1.0/projects/TM/repos/catalog"
LFS = "/scm/tm/catalog.git/info/lfs/objects/batch"


class FakeBitbucket:
    def __init__(self, files: dict[str, bytes] | None = None, user: str = "user-a",
                 block_merge: str = "", writable: list[tuple[str, str, str]] | None = None
                 ) -> None:
        self.user = user
        # (project key, slug, name) of the repos the user may write to
        self.writable = [("TM", "catalog", "catalog")] if writable is None else writable
        self.block_merge = block_merge
        self.branches: dict[str, dict[str, bytes]] = {"main": dict(files or {})}
        self.heads = {"main": "c0"}
        self.snapshots = {"c0": dict(self.branches["main"])}  # commit -> files
        self.bases: dict[str, str] = {}  # branch -> commit it was created at
        self.commits = 0
        self.prs: list[dict] = []
        self.lfs: dict[str, bytes] = {}
        self.requests: list[str] = []

    def commit(self, branch: str) -> None:
        self.commits += 1
        self.heads[branch] = f"c{self.commits}"
        self.snapshots[self.heads[branch]] = dict(self.branches[branch])

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def _json(self, code: int, body: object) -> httpx.Response:
        return httpx.Response(code, json=body, headers={"X-AUSERNAME": self.user})

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        path = unquote(url.path)
        self.requests.append(f"{request.method} {path}")
        if path == "/rest/api/1.0/repos":
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            assert query["permission"] == "REPO_WRITE"
            repos = [{"slug": slug, "name": name, "project": {"key": key}}
                     for key, slug, name in self.writable
                     if "name" not in query or query["name"].lower() in name.lower()]
            start, limit = int(query.get("start", 0)), int(query["limit"])
            page = repos[start:start + limit]
            last = start + limit >= len(repos)
            return self._json(200, {"values": page, "isLastPage": last,
                                    **({} if last else {"nextPageStart": start + limit})})
        if path == f"{REST}/commits":
            return self._json(200, {"values": [{"id": self.heads["main"]}]})
        if path == LFS:
            body = json.loads(request.content)
            obj = body["objects"][0]
            if body["operation"] == "download":
                return self._json(200, {"objects": [{**obj, "actions": {"download": {
                    "href": f"{BASE}/lfs-download/{obj['oid']}"}}}]})
            if obj["oid"] in self.lfs:
                return self._json(200, {"objects": [{**obj}]})
            href = f"{BASE}/lfs-upload/{obj['oid']}"
            return self._json(200, {"objects": [{**obj, "actions": {
                "upload": {"href": href, "header": {"X-Token": "t"}},
                "verify": {"href": f"{BASE}/lfs-verify"}}}]})
        raw = "/projects/TM/repos/catalog/raw/"
        if path.startswith(raw):
            at = parse_qs(url.query).get("at", ["main"])[0].removeprefix("refs/heads/")
            files = self.branches.get(at) or self.snapshots.get(at, {})
            rel = path.removeprefix(raw)
            if rel not in files:
                return self._json(404, {"errors": [{"message": "no such file"}]})
            return httpx.Response(200, content=files[rel], headers={"X-AUSERNAME": self.user})
        if path.startswith("/lfs-download/"):
            return httpx.Response(200, content=self.lfs[path.rsplit("/", 1)[1]])
        if path.startswith("/lfs-upload/"):
            data = request.read()
            oid = path.rsplit("/", 1)[1]
            assert hashlib.sha256(data).hexdigest() == oid
            self.lfs[oid] = data
            return httpx.Response(200)
        if path == "/lfs-verify":
            return httpx.Response(200)
        if path == f"{REST}/branches" and request.method == "POST":
            body = json.loads(request.content)
            name = body["name"]
            if name in self.branches:
                return self._json(409, {"errors": [{"message": "exists"}]})
            start = body["startPoint"]
            commit = (self.heads[start.removeprefix("refs/heads/")]
                      if start.startswith("refs/heads/") else start)
            if commit not in self.snapshots:
                return self._json(400, {"errors": [{"message": f"unknown commit {start}"}]})
            self.branches[name] = dict(self.snapshots[commit])
            self.heads[name] = self.bases[name] = commit
            return self._json(200, {"id": f"refs/heads/{name}", "latestCommit": self.heads[name]})
        if path.startswith(f"{REST}/browse/") and request.method == "PUT":
            rel = path.removeprefix(f"{REST}/browse/")
            form, content = _multipart(request)
            files = self.branches[form["branch"]]
            if rel in files and form.get("sourceCommitId") != self.heads[form["branch"]]:
                return self._json(409, {"errors": [{"message": "file exists"}]})
            files[rel] = content
            self.commit(form["branch"])
            return self._json(200, {"id": self.heads[form["branch"]], "message": form["message"]})
        if path == f"{REST}/pull-requests" and request.method == "POST":
            body = json.loads(request.content)
            pr = {"id": len(self.prs) + 1, "version": 0, "title": body["title"],
                  "description": body["description"], "state": "OPEN",
                  "from": body["fromRef"]["id"].removeprefix("refs/heads/"),
                  "links": {"self": [{"href": f"{BASE}/pr/{len(self.prs) + 1}"}]}}
            self.prs.append(pr)
            return self._json(201, pr)
        if path.endswith("/merge") and request.method == "POST":
            pr = self.prs[int(path.split("/")[-2]) - 1]
            if parse_qs(url.query).get("version") != [str(pr["version"])]:
                return self._json(409, {"errors": [{"message": "out of date"}]})
            if self.block_merge:
                return self._json(409, {"errors": [{"message": "Merge blocked", "vetoes": [
                    {"summaryMessage": self.block_merge}]}]})
            branch, main = self.branches[pr["from"]], self.branches["main"]
            base = self.snapshots[self.bases[pr["from"]]]
            changed = {p for p in branch if branch[p] != base.get(p)}
            conflicts = [p for p in changed if main.get(p) != base.get(p)
                         and main.get(p) != branch[p]]  # changed on main since too
            if conflicts:
                return self._json(409, {"errors": [{"message": "Merge conflict", "vetoes": [
                    {"summaryMessage": f"conflict in {', '.join(sorted(conflicts))}"}]}]})
            for p in changed:
                main[p] = branch[p]
            self.commit("main")
            pr["state"] = "MERGED"
            return self._json(200, pr)
        return self._json(404, {"errors": [{"message": f"no route {path}"}]})


def _multipart(request: httpx.Request) -> tuple[dict[str, str], bytes]:
    ctype = request.headers["Content-Type"]
    boundary = ctype.split("boundary=")[1].encode()
    form, content = {}, b""
    for part in request.read().split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, body = part.split(b"\r\n\r\n", 1)
        body = body.removesuffix(b"\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        if b"filename=" in head:
            content = body
        else:
            form[name] = body.decode()
    return form, content
