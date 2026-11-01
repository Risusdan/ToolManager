"""P0: verify the Bitbucket Server write APIs the publish wizard relies on (docs/DESIGN.md §8.1).

WRITES TO THE SERVER: run it only against a TEST catalog repo. It publishes a dummy tool
"p0-publish-check" (a new version each run) the same way the wizard does, step by step:
write access, LFS upload, branch, new files, editing an existing file, LFS download from the
branch, pull request and merge.

Usage (token is read from TOOLMANAGER_TOKEN or prompted; it needs repository write):
    uv run python scripts/p0_check_publish.py --url https://bitbucket.example.com \
        --project TM --repo test-catalog --confirm test-catalog [--branch main] [--no-merge]

--confirm must repeat the repo name: a guard against pointing it at the real catalog.
--no-merge leaves the pull request open instead of merging it (decline it by hand later).
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from toolmanager.backends.bitbucket_server import BitbucketPublisher, BitbucketServerRepository
from toolmanager.core.publish import MergeBlocked, lfs_pointer
from toolmanager.core.repository import RepositoryError

TOOL = "p0-publish-check"
results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> object:
    start = time.perf_counter()
    try:
        value, detail = fn()
        results.append((name, True, f"{detail} ({time.perf_counter() - start:.2f}s)"))
        return value
    except (RepositoryError, MergeBlocked, OSError, KeyError, ValueError) as e:
        results.append((name, False, f"{type(e).__name__}: {e}"))
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--confirm", required=True, help="the repo name again (a TEST repo)")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--no-merge", action="store_true", help="leave the pull request open")
    args = parser.parse_args()
    if args.confirm != args.repo:
        print(f"--confirm must be the repo name ({args.repo}): this script writes to it.")
        return 2

    token = os.environ.get("TOOLMANAGER_TOKEN") or getpass.getpass("HTTP access token: ")
    repo = BitbucketServerRepository(args.url, args.project, args.repo, token, args.branch)
    publisher = BitbucketPublisher(repo)
    tmp = Path(tempfile.mkdtemp(prefix="tm-p0-publish-"))
    version = f"0.0.{int(time.time())}"  # a new version every run
    rel_dir = f"tools/{TOOL}/releases/{version}"
    zip_rel = f"{rel_dir}/{TOOL}-{version}.zip"

    # A small package with unique content, so the LFS upload is never skipped as known.
    package = tmp / "package.zip"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("main.py", f"print('p0 {version}')\n")
        zf.writestr("noise.bin", os.urandom(64 * 1024))
    oid, size, pointer = lfs_pointer(package)

    def access():
        writable = publisher.can_write()
        if not writable:
            raise ValueError(f"user={publisher.current_user()} has no write access: the publish "
                             "wizard stays hidden for this account and publishing would fail")
        return None, f"user={publisher.current_user()}, can_write=True"

    check("1. Account and write access (GET /rest/api/1.0/repos?permission=REPO_WRITE)", access)
    revision = check("2. Latest revision of the target branch",
                     lambda: (r := repo.latest_revision(), f"rev={r[:12]}"))
    if not revision:
        return report(tmp)

    uploaded = check("3. Git LFS upload (batch API, PUT, verify)",
                     lambda: (publisher.upload_lfs(package, oid, size) or True,
                              f"oid={oid[:12]}, {size / 1024:.0f} KiB"))
    branch = check("4. Create a branch at the synced commit (POST .../branches)",
                   lambda: (b := publisher.create_branch(f"publish/{TOOL}-{version}", revision),
                            f"branch={b}"))
    if not (uploaded and branch):
        return report(tmp)

    tool_toml = (f'id = "{TOOL}"\nname = "P0 publish check"\nsummary = "{version}"\n'
                 f'category = "misc"\nowners = ["{publisher.current_user()}"]\n'
                 'type = "python"\n')
    release_toml = (f'version = "{version}"\ndate = {time.strftime("%Y-%m-%d")}\n'
                    'entry = "main.py"\nchangelog = "P0 check"\n')

    def commit_new():
        for rel, content in ((f"tools/{TOOL}/tool.toml", tool_toml.encode()),
                             (f"{rel_dir}/release.toml", release_toml.encode()),
                             (zip_rel, pointer)):
            publisher.commit_file(branch, rel, content, f"P0 publish check: {rel}")
        return True, "tool.toml, release.toml and the LFS pointer committed (browse API PUT)"

    committed = check("5. Commit new files, one per commit", commit_new)

    def commit_edit():
        edited = tool_toml.replace(f'summary = "{version}"', f'summary = "{version} edited"')
        publisher.commit_file(branch, f"tools/{TOOL}/tool.toml", edited.encode(),
                              "P0 publish check: edit tool.toml")
        return True, "existing file edited (409 first, then sourceCommitId)"

    if committed:
        check("6. Edit an existing file", commit_edit)

        def download():
            out = repo.download_artifact(f"refs/heads/{branch}", zip_rel, tmp / "back.zip")
            if hashlib.sha256(out.read_bytes()).hexdigest() != oid:
                raise ValueError("downloaded content differs from the upload")
            return None, "content matches the upload (sha256)"

        check("7. Download the package back from the branch (raw + LFS)", download)

        pr = check("8. Open a pull request",
                   lambda: (h := publisher.open_pull_request(
                       branch, f"[P0] {TOOL} {version}", "P0 publish check, safe to merge"),
                            f"{h[0] or '(no link)'}"))
        if pr and not args.no_merge:
            check("9. Merge the pull request",
                  lambda: (publisher.merge_pull_request(pr[1]), "merged"))
        elif pr:
            print(f"Pull request left open: {pr[0]}")
    return report(tmp)


def report(tmp: Path) -> int:
    print()
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print(f"\nTemporary files: {tmp}")
    print("A FAIL at step 9 that says the merge needs approvals is expected when the branch "
          "requires reviews: the wizard then keeps the pull request open.")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
