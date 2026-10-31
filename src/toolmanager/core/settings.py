"""User settings stored in settings.toml. Secrets (tokens) live in core.credentials, not here.

Organisation-specific values (Bitbucket URL, PyPI mirror, ...) only ever come from
files at runtime; they are never hard-coded in the source tree:

- defaults.toml next to ToolManager.exe: written by whoever packages ToolManager for
  the department, so colleagues do not have to type the server address
- settings.toml in the user's data folder: the user's own choices, overriding defaults
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit

import tomli_w


@dataclass
class BitbucketSettings:
    base_url: str = ""  # e.g. https://bitbucket.example.com
    project: str = ""
    repo: str = ""
    branch: str = "main"


@dataclass
class Settings:
    # "bitbucket" is production; "local" reads a catalog folder directly (dev / demo).
    backend: str = "bitbucket"
    local_catalog: str = ""
    bitbucket: BitbucketSettings = field(default_factory=BitbucketSettings)
    pypi_index_url: str = ""
    # Python script tools: "uv" (default) or "venv" (venv + pip fallback). Empty paths mean
    # the interpreter and uv shipped with ToolManager.
    env_backend: str = "uv"
    python_exe: str = ""
    uv_exe: str = ""
    language: str = "zh_TW"
    check_interval_hours: int = 4
    trash_retention_days: int = 7
    # Main colour as #rrggbb; empty = the standard blue. Set in the customer edition's
    # defaults.toml so it is easy to tell apart from the internal one (docs/CUSTOMER.md).
    accent: str = ""

    @classmethod
    def load(cls, path: Path | None, defaults: Path | None = None) -> Settings:
        """Built-in defaults <- defaults.toml <- settings.toml (later wins, per field)."""
        merged: dict = {}
        for source in (defaults, path):
            if source is not None and source.is_file():
                with source.open("rb") as f:
                    data = tomllib.load(f)
                bb = {**merged.get("bitbucket", {}), **data.pop("bitbucket", {})}
                merged.update(data)
                merged["bitbucket"] = bb
        bb_fields = {f.name for f in fields(BitbucketSettings)}
        bitbucket = BitbucketSettings(
            **{k: v for k, v in merged.pop("bitbucket", {}).items() if k in bb_fields}
        )
        known = {k: v for k, v in merged.items() if k in cls.__dataclass_fields__}
        return cls(**known, bitbucket=bitbucket)

    def save(self, path: Path, baseline: Settings | None = None) -> None:
        """Write only values that differ from baseline (built-in + department defaults).

        Unchanged values stay out of settings.toml, so a later change to defaults.toml
        (e.g. a new server address) still reaches users who never touched that field.
        """
        data = asdict(self)
        if baseline is not None:
            base = asdict(baseline)
            bb = {k: v for k, v in data.pop("bitbucket").items() if v != base["bitbucket"][k]}
            data = {k: v for k, v in data.items() if v != base[k]}
            if bb:
                data["bitbucket"] = bb
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(tomli_w.dumps(data).encode("utf-8"))
        os.replace(tmp, path)  # never leave a half-written settings file

    def server_configured(self) -> bool:
        if self.backend == "local":
            return bool(self.local_catalog)
        bb = self.bitbucket
        return bool(bb.base_url and bb.project and bb.repo)


def normalize_base_url(url: str) -> str:
    """Validate and normalise a server URL; raises ValueError with a short reason."""
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("must start with https:// (or http://) and include a host name")
    if parts.query or parts.fragment:
        raise ValueError("must not contain ? or #")
    return url
