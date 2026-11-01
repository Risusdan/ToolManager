"""Write ToolManager's .ico in an edition's main colour (see src/toolmanager/core/icon.py).

    uv run python scripts/make_icon.py --out dist/ToolManager.ico
    uv run python scripts/make_icon.py --defaults customer-defaults.toml --out dist/x.ico

--defaults: take `accent` from that defaults.toml (the customer edition's colour); without
it, or without an accent in it, the icon keeps its own colour.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

from toolmanager.core import icon


def accent_of(defaults: Path | None) -> str:
    if defaults is None:
        return icon.BASE_COLOR
    accent = str(tomllib.loads(defaults.read_text("utf-8")).get("accent", "")).strip()
    if not accent:
        return icon.BASE_COLOR
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        raise SystemExit(f"{defaults}: accent must look like #2a4a6e, not {accent!r}")
    return accent.lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--defaults", type=Path, help="defaults.toml with an accent colour")
    parser.add_argument("--out", type=Path, required=True, help="the .ico to write")
    args = parser.parse_args(argv)
    accent = accent_of(args.defaults)
    icon.write_ico(args.out, accent)
    print(f"{args.out} ({accent})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
