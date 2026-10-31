"""Parse a catalog directory (catalog.toml / tool.toml / release.toml) into models.

A broken tool must not take the whole catalog down for every user, so per-tool
errors are collected and the tool is skipped. Only an invalid catalog.toml is fatal.
"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from packaging.version import Version

from .models import ArgSpec, ArgType, Catalog, Category, JiraSettings, Release, Tool, ToolType

TOOL_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

CATALOG_FILE = "catalog.toml"
TOOL_FILE = "tool.toml"
RELEASE_FILE = "release.toml"
README_FILE = "README.md"


class ManifestError(Exception):
    def __init__(self, path: Path | str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = str(path)
        self.message = message


@dataclass(frozen=True)
class LoadResult:
    catalog: Catalog
    problems: tuple[ManifestError, ...]


def parse_semver(value: Any, path: Path | str, field: str = "version") -> Version:
    if not isinstance(value, str) or not SEMVER_RE.match(value):
        raise ManifestError(path, f"'{field}' must be MAJOR.MINOR.PATCH, got {value!r}")
    return Version(value)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise ManifestError(path, "file not found") from None
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(path, f"invalid TOML: {e}") from None


def _require(data: dict[str, Any], key: str, typ: type, path: Path) -> Any:
    if key not in data:
        raise ManifestError(path, f"missing required field '{key}'")
    value = data[key]
    if not isinstance(value, typ):
        raise ManifestError(path, f"'{key}' must be {typ.__name__}")
    return value


def _enum(enum_cls: type, value: Any, path: Path, field: str) -> Any:
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(e.value for e in enum_cls)
        raise ManifestError(path, f"'{field}' must be one of: {allowed}") from None


def parse_catalog_file(path: Path) -> Catalog:
    data = _read_toml(path)
    categories = []
    for raw in data.get("categories", []):
        categories.append(
            Category(id=_require(raw, "id", str, path), name=_require(raw, "name", str, path))
        )
    if not categories:
        raise ManifestError(path, "at least one [[categories]] entry is required")
    return Catalog(
        schema_version=_require(data, "schema_version", int, path),
        min_client_version=parse_semver(
            data.get("min_client_version", "0.0.0"), path, "min_client_version"
        ),
        admins=tuple(data.get("admins", [])),
        categories=tuple(categories),
        jira=_parse_jira(data.get("jira"), path),
        python_offline_only=_bool(data, "python_offline_only", path),
    )


def _bool(data: dict[str, Any], key: str, path: Path) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise ManifestError(path, f"'{key}' must be true or false")
    return value


def _parse_jira(raw: Any, path: Path) -> JiraSettings | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ManifestError(path, "[jira] must be a table")
    base_url = _require(raw, "base_url", str, path).rstrip("/")
    if not re.match(r"^https?://[^\s/?#]+(/[^\s?#]*)?$", base_url):
        raise ManifestError(path, f"invalid [jira] base_url {base_url!r}")
    key = _require(raw, "project_key", str, path)
    if not re.match(r"^[A-Z][A-Z0-9_]+$", key):
        raise ManifestError(path, f"invalid [jira] project_key {key!r}")
    prefix = raw.get("label_prefix", "tm-")
    if not isinstance(prefix, str) or not re.match(r"^[A-Za-z0-9_.-]*$", prefix):
        raise ManifestError(path, "[jira] label_prefix may only use letters, digits, '_', '.', '-'")
    return JiraSettings(
        base_url=base_url,
        project_key=key,
        project_id=_require(raw, "project_id", int, path),
        issue_type_id=_require(raw, "issue_type_id", int, path),
        label_prefix=prefix,
    )


ARG_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_DEFAULT_TYPES = {
    ArgType.INT: (int,),
    ArgType.FLOAT: (int, float),
    ArgType.BOOL: (bool,),
}


def _parse_arg(raw: dict[str, Any], path: Path) -> ArgSpec:
    arg_type = _enum(ArgType, _require(raw, "type", str, path), path, "args.type")
    name = _require(raw, "name", str, path)
    if not ARG_NAME_RE.match(name):
        raise ManifestError(path, f"invalid arg name {name!r} (letters, digits, '_', '-')")
    choices = tuple(str(c) for c in raw.get("choices", []))
    if arg_type is ArgType.CHOICE and not choices:
        raise ManifestError(path, "args of type 'choice' require 'choices'")
    default = raw.get("default")
    expected = _DEFAULT_TYPES.get(arg_type, (str,))
    if default is not None and (
        not isinstance(default, expected)
        or (arg_type is not ArgType.BOOL and isinstance(default, bool))
    ):
        raise ManifestError(path, f"default of arg '{name}' does not match type '{arg_type}'")
    if arg_type is ArgType.CHOICE and default is not None and default not in choices:
        raise ManifestError(path, f"default of arg '{name}' is not one of its choices")
    flag = raw.get("flag", "")
    if flag and (not isinstance(flag, str) or not re.match(r"^--?[A-Za-z0-9][\w-]*$", flag)):
        raise ManifestError(path, f"invalid flag {flag!r} for arg '{name}'")
    positional = bool(raw.get("positional", False))
    if positional and arg_type is ArgType.BOOL:
        raise ManifestError(path, f"bool arg '{name}' cannot be positional")
    return ArgSpec(
        name=name,
        type=arg_type,
        label=raw.get("label", name),
        default=default,
        choices=choices,
        flag=flag,
        positional=positional,
        required=bool(raw.get("required", False)),
        help=raw.get("help", ""),
    )


def parse_release_file(path: Path, tool_id: str, tool_type: ToolType) -> Release:
    data = _read_toml(path)
    version = parse_semver(_require(data, "version", str, path), path)
    if path.parent.name != str(version):
        raise ManifestError(path, f"folder name '{path.parent.name}' != version '{version}'")
    date = _require(data, "date", dt.date, path)
    min_client = data.get("min_client_version")
    important_note = data.get("important_note", "")
    if not isinstance(important_note, str):
        raise ManifestError(path, "'important_note' must be str")
    return Release(
        version=version,
        date=date,
        changelog=data.get("changelog", "").strip(),
        entry=_require(data, "entry", str, path),
        important=bool(data.get("important", False)),
        important_note=important_note.strip(),
        min_client_version=(
            parse_semver(min_client, path, "min_client_version") if min_client else None
        ),
        installer_args=data.get("installer_args", ""),
        python=data.get("python", "") if tool_type is ToolType.PYTHON else "",
        gui=bool(data.get("gui", False)),
        args=tuple(_parse_arg(a, path) for a in data.get("args", [])),
        artifact=f"tools/{tool_id}/releases/{version}/{tool_id}-{version}.zip",
    )


def parse_tool_dir(tool_dir: Path, catalog: Catalog) -> Tool:
    path = tool_dir / TOOL_FILE
    data = _read_toml(path)
    tool_id = _require(data, "id", str, path)
    if not TOOL_ID_RE.match(tool_id):
        raise ManifestError(path, f"invalid id {tool_id!r} (lowercase letters, digits, '-')")
    if tool_id != tool_dir.name:
        raise ManifestError(path, f"id '{tool_id}' != folder name '{tool_dir.name}'")
    category = _require(data, "category", str, path)
    if catalog.category(category) is None:
        raise ManifestError(path, f"unknown category '{category}'")
    owners = _require(data, "owners", list, path)
    if not owners:
        raise ManifestError(path, "'owners' must not be empty")
    tool_type = _enum(ToolType, _require(data, "type", str, path), path, "type")

    releases = [
        parse_release_file(release_file, tool_id, tool_type)
        for release_file in sorted((tool_dir / "releases").glob(f"*/{RELEASE_FILE}"))
    ]
    releases.sort(key=lambda r: r.version, reverse=True)

    readme_path = tool_dir / README_FILE
    return Tool(
        id=tool_id,
        name=_require(data, "name", str, path),
        summary=_require(data, "summary", str, path),
        category=category,
        owners=tuple(owners),
        type=tool_type,
        issue_url=data.get("issue_url", ""),
        readme=readme_path.read_text(encoding="utf-8") if readme_path.is_file() else "",
        releases=tuple(releases),
    )


def load_catalog(root: Path) -> LoadResult:
    """Load a catalog checkout/cache directory. Raises ManifestError if catalog.toml is bad."""
    catalog = parse_catalog_file(root / CATALOG_FILE)
    tools: list[Tool] = []
    problems: list[ManifestError] = []
    tools_dir = root / "tools"
    if tools_dir.is_dir():
        for tool_dir in sorted(p for p in tools_dir.iterdir() if p.is_dir()):
            try:
                tools.append(parse_tool_dir(tool_dir, catalog))
            except ManifestError as e:
                problems.append(e)
    return LoadResult(
        catalog=replace(catalog, tools=tuple(tools)),
        problems=tuple(problems),
    )
