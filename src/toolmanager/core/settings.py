"""User settings stored in settings.toml. Secrets (tokens) live in core.credentials, not here.

Organisation-specific values (Bitbucket URL, PyPI mirror, ...) only ever come from
this file at runtime; they are never hard-coded in the source tree.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomli_w


@dataclass
class BitbucketSettings:
    base_url: str = ""  # e.g. https://bitbucket.example.com
    project: str = ""
    repo: str = ""
    branch: str = "main"


@dataclass
class Settings:
    # "local" reads a catalog folder directly (dev / demo); "bitbucket" is production.
    backend: str = "local"
    local_catalog: str = ""
    bitbucket: BitbucketSettings = field(default_factory=BitbucketSettings)
    pypi_index_url: str = ""
    language: str = "zh_TW"
    check_interval_hours: int = 4
    trash_retention_days: int = 7

    @classmethod
    def load(cls, path: Path) -> Settings:
        if not path.is_file():
            return cls()
        with path.open("rb") as f:
            data = tomllib.load(f)
        bitbucket = BitbucketSettings(**data.pop("bitbucket", {}))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known, bitbucket=bitbucket)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(tomli_w.dumps(asdict(self)).encode("utf-8"))
