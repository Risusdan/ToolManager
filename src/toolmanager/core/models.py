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
    """A parameter of a Python script, rendered as a form field.

    Command-line mapping (matches argparse conventions): "--<name> <value>" by default,
    a custom "flag" instead of "--<name>", a bare flag for a ticked bool, or the value alone
    when "positional" is set. Positional arguments keep their order in the manifest.
    """

    name: str
    type: ArgType
    label: str
    default: str | int | float | bool | None = None
    choices: tuple[str, ...] = ()
    flag: str = ""
    positional: bool = False
    required: bool = False
    help: str = ""

    @property
    def cli_flag(self) -> str:
        return self.flag or f"--{self.name}"


@dataclass(frozen=True)
class Release:
    version: Version
    date: dt.date
    changelog: str
    entry: str
    important: bool = False
    # One sentence telling users why they should update soon (shown with "important").
    important_note: str = ""
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
    readme: str = ""
    # Sorted newest first.
    releases: tuple[Release, ...] = ()

    @property
    def latest(self) -> Release | None:
        return self.releases[0] if self.releases else None


@dataclass(frozen=True)
class JiraSettings:
    """Where problem reports go ([jira] in catalog.toml). Tickets are told apart by label."""

    base_url: str
    project_key: str
    project_id: int  # numeric id: Jira's prefilled create page needs it
    issue_type_id: int  # usually "Bug"
    label_prefix: str = "tm-"

    def label_for(self, tool_id: str) -> str:
        return f"{self.label_prefix}{tool_id}"


@dataclass(frozen=True)
class Catalog:
    schema_version: int
    min_client_version: Version
    admins: tuple[str, ...]
    categories: tuple[Category, ...]
    tools: tuple[Tool, ...] = field(default=())
    jira: JiraSettings | None = None
    # Python tools must install from their own wheels/ (catalogs for customers).
    python_offline_only: bool = False

    def tool(self, tool_id: str) -> Tool | None:
        return next((t for t in self.tools if t.id == tool_id), None)

    def category(self, category_id: str) -> Category | None:
        return next((c for c in self.categories if c.id == category_id), None)
