"""Build the small demo zips for examples/catalog (dummy files, not real programs)."""

from __future__ import annotations

import tomllib
import zipfile
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "examples" / "catalog"


def main() -> None:
    for release_toml in sorted(CATALOG.glob("tools/*/releases/*/release.toml")):
        with release_toml.open("rb") as f:
            release = tomllib.load(f)
        tool_id = release_toml.parents[2].name
        version = release["version"]
        out = release_toml.parent / f"{tool_id}-{version}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            # Fixed timestamps keep the zips byte-identical across runs.
            def add(name: str, text: str) -> None:
                zf.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), text)

            add(release["entry"], f"placeholder for {tool_id} {version}\n")
            add("README.txt", f"{tool_id} {version} demo package\n")
        print(out.relative_to(CATALOG))


if __name__ == "__main__":
    main()
