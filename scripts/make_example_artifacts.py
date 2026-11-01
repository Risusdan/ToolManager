"""Build the demo zips for examples/catalog: real sources from examples/sources/<tool-id>/
when present, otherwise a dummy placeholder package."""

from __future__ import annotations

import tomllib
import zipfile
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "examples" / "catalog"
SOURCES = CATALOG.parent / "sources"  # real programs for example tools, by tool id


def main() -> None:
    for release_toml in sorted(CATALOG.glob("tools/*/releases/*/release.toml")):
        with release_toml.open("rb") as f:
            release = tomllib.load(f)
        tool_id = release_toml.parents[2].name
        version = release["version"]
        out = release_toml.parent / f"{tool_id}-{version}.zip"
        sources = SOURCES / tool_id
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            # Fixed timestamps keep the zips byte-identical across runs.
            def add(name: str, data: str | bytes) -> None:
                zf.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), data)

            if sources.is_dir():
                # A real example program (e.g. a Python script tool).
                for path in sorted(p for p in sources.rglob("*") if p.is_file()):
                    if "__pycache__" not in path.parts:
                        add(path.relative_to(sources).as_posix(), path.read_bytes())
            else:
                add(release["entry"], f"placeholder for {tool_id} {version}\n")
                add("README.txt", f"{tool_id} {version} demo package\n")
        print(out.relative_to(CATALOG))


if __name__ == "__main__":
    main()
