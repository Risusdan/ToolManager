"""P0: verify the Bitbucket Server APIs ToolManager relies on (docs/DESIGN.md §7).

Read-only: this script never writes to the server.

Usage (token is read from TOOLMANAGER_TOKEN or prompted):
    uv run python scripts/p0_check_bitbucket.py --url https://bitbucket.example.com \
        --project TM --repo tool-catalog [--branch main] \
        [--artifact tools/x/releases/1.0.0/x-1.0.0.zip]
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from toolmanager.backends.bitbucket_server import BitbucketServerRepository
from toolmanager.core.lfs import parse_pointer
from toolmanager.core.manifest import ManifestError, load_catalog
from toolmanager.core.repository import RepositoryError

results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> object:
    start = time.perf_counter()
    try:
        value, detail = fn()
        results.append((name, True, f"{detail} ({time.perf_counter() - start:.2f}s)"))
        return value
    except (RepositoryError, ManifestError, OSError, KeyError, ValueError) as e:
        results.append((name, False, str(e)))
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--artifact", help="repo path of an LFS-tracked file to download")
    args = parser.parse_args()

    token = os.environ.get("TOOLMANAGER_TOKEN") or getpass.getpass("HTTP access token: ")
    repo = BitbucketServerRepository(args.url, args.project, args.repo, token, args.branch)
    tmp = Path(tempfile.mkdtemp(prefix="tm-p0-"))

    rev = check(
        "1. REST commits API (latest revision)",
        lambda: (r := repo.latest_revision(), f"rev={r[:12]}, user={repo.username}"),
    )
    if rev:
        def fetch():
            dest = tmp / "catalog"
            dest.mkdir()
            repo.fetch_catalog(rev, dest)
            size = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
            files = sum(1 for p in dest.rglob("*") if p.is_file())
            return dest, f"{files} files, {size / 1024:.0f} KiB extracted"

        dest = check("2. REST archive API (catalog zip)", fetch)
        if dest:
            def lfs_in_archive():
                big = [p for p in dest.rglob("*.zip") if p.is_file()]
                pointers = [p for p in big if parse_pointer(p.read_bytes()[:1024])]
                resolved = [p for p in big if p not in pointers]
                if resolved:
                    raise ValueError(
                        f"{len(resolved)} zip(s) came back as full content: the archive API "
                        "resolves LFS, so catalog sync would download every artifact"
                    )
                return None, f"{len(pointers)} LFS file(s) returned as small pointers"

            check("3. Archive keeps LFS files as pointers", lfs_in_archive)

            def parse():
                result = load_catalog(dest)
                problems = "; ".join(str(p) for p in result.problems) or "none"
                return None, f"{len(result.catalog.tools)} tool(s), problems: {problems}"

            check("4. Catalog parses", parse)

        if args.artifact:
            def download():
                out = repo.download_artifact(rev, args.artifact, tmp / "artifact.bin")
                ok_zip = zipfile.is_zipfile(out)
                return None, f"{out.stat().st_size / 1024:.0f} KiB, valid zip={ok_zip}"

            check("5. Raw + Git LFS batch download (sha256 verified)", download)

    print()
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print(f"\nTemporary files: {tmp}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
