"""Check the internal catalog repo before a pull request is merged (docs/DESIGN.md §8.3).

Run it in a checkout of the catalog repo, on the branch of the pull request:

    uv run python scripts/check_catalog.py <catalog folder> [--base origin/main] [--author id]

--base    the target branch: also checks the change (published releases unchanged, new
          versions newer, owners). Without it only the files themselves are checked.
--author  the Bitbucket account that publishes: must be an owner of each changed tool
          (admins may change anything).

Exit code 0 = no errors (warnings and notes may remain), 1 = errors found. The same checks
run in the publish wizard before sending.
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from toolmanager.core.catalog_check import check_catalog


def _git(folder: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True)


def lfs_checker(folder: Path):
    """lfs_tracked(path) from .gitattributes via git, or None outside a git checkout."""
    if _git(folder, "rev-parse", "--git-dir").returncode != 0:
        return None

    def tracked(path: str) -> bool | None:
        out = _git(folder, "check-attr", "filter", "--", path)
        if out.returncode != 0:
            return None
        return out.stdout.decode().strip().endswith(": lfs")

    return tracked


def export_ref(folder: Path, ref: str, dest: Path) -> None:
    """The catalog as it is on ref (LFS files stay pointers, which is all the check needs)."""
    # Run inside the folder, git archive only takes that folder (the catalog may be a subfolder).
    out = _git(folder, "archive", "--format=tar", ref)
    if out.returncode != 0:
        raise SystemExit(f"git archive {ref} failed: {out.stderr.decode().strip()}")
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
        tar.extractall(dest, filter="data")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a catalog change before merging it.")
    parser.add_argument("catalog", type=Path, help="checkout of the catalog repo")
    parser.add_argument("--base", help="target branch / commit, e.g. origin/main")
    parser.add_argument("--author", default="", help="Bitbucket account publishing the change")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        base = None
        if args.base:
            base = Path(tmp)
            export_ref(args.catalog, args.base, base)
        report = check_catalog(args.catalog, base, args.author, lfs_checker(args.catalog))

    for text in report.errors:
        print(f"[錯誤] {text}")
    for text in report.warnings:
        print(f"[注意] {text}")
    for text in report.notes:
        print(f"[說明] {text}")
    print(f"\n{len(report.errors)} 個錯誤，{len(report.warnings)} 個注意事項")
    if not args.base:
        print("（未提供 --base：沒有檢查版本號遞增、已發布版本是否被修改、owners）")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
