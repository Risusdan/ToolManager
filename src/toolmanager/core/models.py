"""Domain models for the catalog (see docs/DESIGN.md §3)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum

from packaging.version import Version


class ToolType(StrEnum):
    EXE = "exe"
    INSTALLER = "installer"
    PYTHON = "python"


class ArgType(StrEnum):
    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    FILE = "file"
    FOLDER = "folder"
    CHOICE = "choice"


@dataclass(frozen=True)
class Category:
    id: str
    name: str


@dataclass(frozen=True)
class ArgSpec:
    """A parameter of a Python script, rendered as a form field."""

    name: str
    type: ArgType
    label: str
    default: str | int | float | bool | None = None
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Release:
    version: Version
    date: dt.date
    changelog: str
    entry: str
    important: bool = False
    min_client_version: Version | None = None
    # installer only
    installer_args: str = ""
    # python only
    python: str = ""
    gui: bool = False
    args: tuple[ArgSpec, ...] = ()
    # Artifact path relative to the catalog root, e.g. tools/x/releases/1.0.0/x-1.0.0.zip
    artifact: str = ""


@dataclass(frozen=True)
class Tool:
    id: str
    name: str
    summary: str
    category: str
    owners: tuple[str, ...]
    type: ToolType
    issue_url: str = ""
    readme: str = ""
    # Sorted newest first.
    releases: tuple[Release, ...] = ()

    @property
    def latest(self) -> Release | None:
        return self.releases[0] if self.releases else None


@dataclass(frozen=True)
class Catalog:
    schema_version: int
    min_client_version: Version
    admins: tuple[str, ...]
    categories: tuple[Category, ...]
    tools: tuple[Tool, ...] = field(default=())

    def tool(self, tool_id: str) -> Tool | None:
        return next((t for t in self.tools if t.id == tool_id), None)

    def category(self, category_id: str) -> Category | None:
        return next((c for c in self.categories if c.id == category_id), None)
