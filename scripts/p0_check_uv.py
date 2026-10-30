"""P0: verify uv can build a script environment using only the internal PyPI mirror.

Checks that no interpreter download is attempted (UV_PYTHON_DOWNLOADS=never) and
that packages install from the mirror (docs/DESIGN.md §5.2).

Usage:
    uv run python scripts/p0_check_uv.py --index-url https://pypi.example.com/simple \
        [--python C:\\path\\to\\python.exe] [--uv C:\\path\\to\\uv.exe] [--package numpy]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-url", required=True)
    parser.add_argument("--python", default=sys.executable, help="interpreter to build from")
    parser.add_argument("--uv", default=shutil.which("uv") or "uv")
    parser.add_argument("--package", default="numpy")
    args = parser.parse_args()

    env = dict(os.environ)
    env.update(
        UV_PYTHON_DOWNLOADS="never",
        UV_INDEX_URL=args.index_url,
        UV_NO_CONFIG="1",  # ignore any developer uv.toml / pyproject settings
    )
    env_dir = Path(tempfile.mkdtemp(prefix="tm-p0-env-")) / "venv"
    py = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    steps = [
        ("create venv", [args.uv, "venv", "--python", args.python, str(env_dir)]),
        ("install", [args.uv, "pip", "install", "--python", str(py), args.package]),
        ("import", [str(py), "-c", f"import {args.package}; print({args.package}.__version__)"]),
    ]
    for name, cmd in steps:
        start = time.perf_counter()
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        elapsed = time.perf_counter() - start
        status = "PASS" if proc.returncode == 0 else "FAIL"
        print(f"[{status}] {name} ({elapsed:.1f}s)")
        if proc.returncode != 0:
            print(proc.stdout + proc.stderr)
            return 1
        if name == "import":
            print(f"       {args.package} {proc.stdout.strip()}")
    print(f"\nEnvironment: {env_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
