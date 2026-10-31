"""Publishing a tool or a new version (docs/DESIGN.md §8). Shared by the wizard and a CLI.

1. prepare(): write the new release (and tool.toml / README.md / images when they change)
   into a copy of the synced catalog, and package the program as a zip.
2. check(): the same rules as scripts/check_catalog.py runs before merging.
3. submit(): through a Publisher (Bitbucket REST API, or a local folder in development):
   upload the zip to Git LFS, commit the changed files on a new branch, open a pull request
   and, for a new version of an existing tool, merge it right away. A new tool waits for an
   admin to approve the pull request.

Only files that differ from the synced catalog are committed, so a release never touches
other tools and several people can publish at the same time.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import shutil
import tomllib
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

from .catalog_check import IMAGE_RE, Report, check_publish
from .lfs import POINTER_VERSION
from .manifest import (
    README_FILE,
    RELEASE_FILE,
    SEMVER_RE,
    TOOL_FILE,
    TOOL_ID_RE,
    ManifestError,
    parse_arg,
)
from .models import ArgSpec, ArgType, Catalog, Tool, ToolType

# Never packaged from a source folder: caches, environments, version control.
SKIP_NAMES = {"__pycache__", ".venv", "venv", ".git", ".svn", ".idea", ".vscode", ".pytest_cache",
              ".mypy_cache", ".ruff_cache", "Thumbs.db", ".DS_Store"}
SKIP_SUFFIXES = (".pyc", ".pyo")


class PublishError(Exception):
    pass


@dataclass
class ToolInfo:
    id: str
    name: str
    summary: str
    category: str
    owners: list[str]
    type: ToolType
    issue_url: str = ""
    readme: str = ""


@dataclass
class ArgDraft:
    """One form field of a Python script as the wizard edits it (strings, as typed).
    to_toml() turns it into a [[args]] table; see models.ArgSpec for the meaning."""

    name: str = ""
    label: str = ""
    type: str = "string"
    default: str | bool = ""
    choices: str = ""  # comma separated
    required: bool = False
    positional: bool = False
    flag: str = ""
    help: str = ""

    @classmethod
    def from_spec(cls, spec: ArgSpec) -> ArgDraft:
        if spec.type is ArgType.BOOL:
            default: str | bool = bool(spec.default)
        else:
            default = "" if spec.default is None else str(spec.default)
        return cls(name=spec.name, label=spec.label if spec.label != spec.name else "",
                   type=str(spec.type), default=default, choices=", ".join(spec.choices),
                   required=spec.required, positional=spec.positional, flag=spec.flag,
                   help=spec.help)

    def to_toml(self) -> dict:
        """Raises ValueError (Chinese message) for a default that does not fit the type."""
        kind = ArgType(self.type)
        data: dict = {"name": self.name.strip(), "type": str(kind)}
        if self.label.strip():
            data["label"] = self.label.strip()
        choices = [c.strip() for c in self.choices.split(",") if c.strip()]
        if kind is ArgType.CHOICE:
            data["choices"] = choices
        default = self.default
        if kind is ArgType.BOOL:
            if default:
                data["default"] = True
        elif str(default).strip():
            text = str(default).strip()
            try:
                data["default"] = (int(text) if kind is ArgType.INT
                                   else float(text) if kind is ArgType.FLOAT else text)
            except ValueError:
                raise ValueError(f"參數「{self.name}」的預設值「{text}」不是數字") from None
        for key in ("required", "positional"):
            if getattr(self, key):
                data[key] = True
        if self.flag.strip():
            data["flag"] = self.flag.strip()
        if self.help.strip():
            data["help"] = self.help.strip()
        return data


def args_problems(drafts: list[ArgDraft]) -> tuple[list[dict], list[str]]:
    """The [[args]] tables, and what is wrong with them (same rules as the catalog)."""
    tables, errors, seen = [], [], set()
    for i, arg in enumerate(drafts, 1):
        if not arg.name.strip():
            errors.append(f"第 {i} 個參數沒有名稱")
            continue
        if arg.name.strip() in seen:
            errors.append(f"參數名稱「{arg.name.strip()}」重複")
        seen.add(arg.name.strip())
        try:
            table = arg.to_toml()
            parse_arg(table, Path(RELEASE_FILE))
        except ValueError as e:
            errors.append(str(e))
            continue
        except ManifestError as e:
            errors.append(e.message)
            continue
        tables.append(table)
    return tables, errors


def arg_specs(drafts: list[ArgDraft]) -> tuple[ArgSpec, ...]:
    """The valid fields as ArgSpecs (for the form preview)."""
    tables, _errors = args_problems(drafts)
    return tuple(parse_arg(t, Path(RELEASE_FILE)) for t in tables)


@dataclass
class ReleaseInfo:
    version: str
    changelog: str
    entry: str
    important: bool = False
    important_note: str = ""
    gui: bool = False  # python: a window of its own, started with pythonw
    installer_args: str = ""  # installer: silent-install switches
    args: list[ArgDraft] = field(default_factory=list)  # python: the run page's form
    python: str = ""  # python: required interpreter version, e.g. ">=3.11" (carried over)
    date: dt.date = field(default_factory=dt.date.today)


@dataclass
class Draft:
    tool: ToolInfo
    release: ReleaseInfo
    source: Path  # the program: a zip, or a folder that gets zipped
    is_new: bool  # a new tool (needs an admin's approval) or a new version of one


@dataclass
class Prepared:
    draft: Draft
    staged: Path  # catalog copy with the new files written in
    files: list[str]  # catalog-relative paths that differ from the synced catalog
    report: Report


# --- helpers for the wizard ---------------------------------------------------------------

def next_version(tool: Tool | None) -> str:
    """The suggested version: the next patch after the latest, or 1.0.0 for a new tool."""
    if tool is None or tool.latest is None:
        return "1.0.0"
    v = tool.latest.version
    return f"{v.major}.{v.minor}.{v.micro + 1}"


def draft_from_tool(tool: Tool, source: Path) -> Draft:
    """A new version of an existing tool: everything carried over from the latest release
    except what changes every time (version, changelog, important)."""
    latest = tool.latest
    return Draft(
        tool=ToolInfo(id=tool.id, name=tool.name, summary=tool.summary, category=tool.category,
                      owners=list(tool.owners), type=tool.type, issue_url=tool.issue_url,
                      readme=tool.readme),
        release=ReleaseInfo(
            version=next_version(tool), changelog="",
            entry=latest.entry if latest else "",
            gui=latest.gui if latest else False,
            installer_args=latest.installer_args if latest else "",
            args=[ArgDraft.from_spec(a) for a in latest.args] if latest else [],
            python=latest.python if latest else ""),
        source=source, is_new=False)


def package_names(source: Path) -> list[str]:
    """Files the package will hold (single top folder removed, like installs do)."""
    if source.is_dir():
        return [p.relative_to(source).as_posix() for p in _folder_files(source)]
    with zipfile.ZipFile(source) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
    roots = {n.split("/", 1)[0] for n in names}
    if len(roots) == 1 and all("/" in n for n in names):
        names = [n.split("/", 1)[1] for n in names]
    return names


def guess_entry(names: list[str], tool_type: ToolType) -> str:
    """The obvious program file, or "" when the author has to choose."""
    if tool_type is ToolType.PYTHON:
        for preferred in ("main.py", "main.pyw", "app.py", "__main__.py"):
            if preferred in names:
                return preferred
        candidates = [n for n in names if "/" not in n and n.endswith((".py", ".pyw"))]
    else:
        candidates = [n for n in names if "/" not in n and n.lower().endswith((".exe", ".msi"))]
    return candidates[0] if len(candidates) == 1 else ""


def _folder_files(folder: Path) -> list[Path]:
    files = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if any(part in SKIP_NAMES for part in rel.parts) or path.suffix in SKIP_SUFFIXES:
            continue
        if path.is_file():
            files.append(path)
    return files


def zip_folder(folder: Path, dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in _folder_files(folder):
            zf.write(path, path.relative_to(folder).as_posix())


# --- prepare and check --------------------------------------------------------------------

def _tool_toml(info: ToolInfo) -> dict:
    data = {"id": info.id, "name": info.name.strip(), "summary": info.summary.strip(),
            "category": info.category, "owners": [o.strip() for o in info.owners if o.strip()],
            "type": str(info.type)}
    if info.issue_url.strip():
        data["issue_url"] = info.issue_url.strip()
    return data


def _release_toml(info: ReleaseInfo, tool_type: ToolType) -> dict:
    data: dict = {"version": info.version, "date": info.date}
    if info.important:
        data["important"] = True
        if info.important_note.strip():
            data["important_note"] = info.important_note.strip()
    data["entry"] = info.entry.strip().replace("\\", "/")
    if tool_type is ToolType.PYTHON and info.python.strip():
        data["python"] = info.python.strip()
    if tool_type is ToolType.PYTHON and info.gui:
        data["gui"] = True
    if tool_type is ToolType.INSTALLER and info.installer_args.strip():
        data["installer_args"] = info.installer_args.strip()
    data["changelog"] = info.changelog.strip() + "\n"
    if tool_type is ToolType.PYTHON:
        tables, _errors = args_problems(info.args)
        if tables:
            data["args"] = tables
    return data


def _basic_errors(draft: Draft, catalog: Catalog) -> list[str]:
    errors = []
    tool, rel = draft.tool, draft.release
    if not TOOL_ID_RE.match(tool.id):
        errors.append("工具 ID 只能用小寫英文、數字與 -（例如 register-viewer）")
    elif draft.is_new and catalog.tool(tool.id) is not None:
        errors.append(f"工具 ID「{tool.id}」已經有人使用")
    if not SEMVER_RE.match(rel.version):
        errors.append("版本號格式為 主版本.次版本.修訂（例如 1.2.0）")
    for label, value in (("名稱", tool.name), ("摘要", tool.summary), ("執行檔", rel.entry),
                         ("更新說明", rel.changelog)):
        if not value.strip():
            errors.append(f"請填寫{label}")
    if not [o for o in tool.owners if o.strip()]:
        errors.append("至少需要一位擁有者（owners）")
    if tool.type is ToolType.PYTHON:
        errors += args_problems(rel.args)[1]
    if rel.important and not rel.important_note.strip():
        errors.append("標為「建議盡快更新」時，請用一句話說明原因")
    if not draft.source.exists():
        errors.append(f"找不到程式：{draft.source}")
    elif draft.source.is_file() and not zipfile.is_zipfile(draft.source):
        errors.append(f"{draft.source.name} 不是 zip 檔")
    return errors


def prepare(draft: Draft, base: Path, workdir: Path, author: str) -> Prepared:
    """Stage the release next to a copy of the synced catalog `base` and check it."""
    from .manifest import load_catalog

    catalog = load_catalog(base).catalog
    staged = workdir / "catalog"
    shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(base, staged)
    errors = _basic_errors(draft, catalog)
    if errors:
        return Prepared(draft, staged, [], Report(errors=errors))

    tool_dir = staged / "tools" / draft.tool.id
    rel_dir = tool_dir / "releases" / draft.release.version
    rel_dir.mkdir(parents=True, exist_ok=True)
    # Rewritten only when the content changes: reformatting alone is not a change to commit.
    tool_file = tool_dir / TOOL_FILE
    wanted = _tool_toml(draft.tool)
    if not tool_file.is_file() or _read_toml(tool_file) != wanted:
        tool_file.write_text(tomli_w.dumps(wanted), "utf-8")
    readme = draft.tool.readme.strip()
    readme_file = tool_dir / README_FILE
    old_readme = readme_file.read_text("utf-8").strip() if readme_file.is_file() else ""
    if readme and readme != old_readme:
        readme_file.write_text(readme + "\n", "utf-8")
    if readme:
        _copy_readme_images(readme, draft.source, tool_dir)
    release = _release_toml(draft.release, draft.tool.type)
    (rel_dir / RELEASE_FILE).write_text(
        tomli_w.dumps(release, multiline_strings=True), "utf-8")
    artifact = rel_dir / f"{draft.tool.id}-{draft.release.version}.zip"
    if draft.source.is_dir():
        zip_folder(draft.source, artifact)
    else:
        shutil.copyfile(draft.source, artifact)

    report = check_publish(staged, base, draft.tool.id, draft.release.version, author)
    return Prepared(draft, staged, changed_files(staged, base, draft.tool.id), report)


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _copy_readme_images(readme: str, source: Path, tool_dir: Path) -> None:
    """Images the README refers to that are not in the catalog yet come from the program's
    folder (README.md written next to assets/ in the source folder)."""
    if not source.is_dir():
        return
    for ref in IMAGE_RE.findall(readme):
        ref = ref.split("#", 1)[0].split("?", 1)[0].removeprefix("./")
        target = (tool_dir / ref).resolve()
        found = (source / ref).resolve()
        if (not target.exists() and target.is_relative_to(tool_dir.resolve())
                and found.is_relative_to(source.resolve()) and found.is_file()):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(found, target)


def changed_files(staged: Path, base: Path, tool_id: str) -> list[str]:
    tool_dir = staged / "tools" / tool_id
    changed = []
    for path in sorted(p for p in tool_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(staged).as_posix()
        old = base / rel
        if not old.is_file() or old.read_bytes() != path.read_bytes():
            changed.append(rel)
    return changed


# --- submit -------------------------------------------------------------------------------

def is_lfs_path(rel: str) -> bool:
    """Release packages go to Git LFS (tools/<id>/releases/<version>/<file>.zip)."""
    parts = rel.split("/")
    return len(parts) == 5 and parts[0] == "tools" and parts[2] == "releases" and (
        rel.endswith(".zip"))


def lfs_pointer(path: Path) -> tuple[str, int, bytes]:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    size = path.stat().st_size
    oid = h.hexdigest()
    return oid, size, f"{POINTER_VERSION}\noid sha256:{oid}\nsize {size}\n".encode()


@dataclass
class SubmitResult:
    url: str  # the pull request ("" when published straight into a local folder)
    merged: bool
    waiting_reason: str = ""  # why it is not merged yet (review needed, merge blocked)


class MergeBlocked(Exception):
    """The server refused to merge (approvals required, conflicts, ...): the PR stays open."""


class Publisher(ABC):
    """Write access to the catalog. Implemented by the backends."""

    # False for a local catalog folder: files land directly, there is nothing to review.
    uses_pull_requests = True

    def known_user(self) -> str:
        """The account name if known without asking the server ("" otherwise)."""
        return ""

    @abstractmethod
    def can_write(self) -> bool:
        """Whether this account may write to the catalog repo. Only such accounts (the tool
        developers) see the publish wizard."""

    @abstractmethod
    def current_user(self) -> str:
        """The account name publishing (checked against owners)."""

    @abstractmethod
    def upload_lfs(self, path: Path, oid: str, size: int) -> None:
        """Store a large file in Git LFS (no-op if the server already has it)."""

    @abstractmethod
    def create_branch(self, name: str) -> str:
        """Create a branch off the catalog branch; returns its name (may add a suffix)."""

    @abstractmethod
    def commit_file(self, branch: str, rel_path: str, content: bytes, message: str) -> None:
        """Add or replace one file on the branch."""

    @abstractmethod
    def open_pull_request(self, branch: str, title: str, description: str) -> tuple[str, object]:
        """Returns (url, handle for merge_pull_request)."""

    @abstractmethod
    def merge_pull_request(self, handle: object) -> None:
        """Merge, or raise MergeBlocked with the server's reason."""


def submit(prepared: Prepared, publisher: Publisher,
           progress: Callable[[str], None] | None = None) -> SubmitResult:
    if not prepared.report.ok:
        raise PublishError("the checks found errors")
    if not prepared.files:
        raise PublishError("nothing to publish: no file differs from the catalog")
    say = progress or (lambda _text: None)
    draft = prepared.draft
    label = f"{draft.tool.id} {draft.release.version}"
    branch = publisher.create_branch(f"publish/{draft.tool.id}-{draft.release.version}")
    for rel in prepared.files:
        path = prepared.staged / rel
        if is_lfs_path(rel):
            say(f"upload:{rel}")
            oid, size, pointer = lfs_pointer(path)
            publisher.upload_lfs(path, oid, size)
            content = pointer
        else:
            content = path.read_bytes()
        say(f"commit:{rel}")
        publisher.commit_file(branch, rel, content, f"Publish {label}: {rel}")
    say("pull-request")
    kind = "新工具" if draft.is_new else "新版本"
    title = f"[{kind}] {draft.tool.name} {draft.release.version}"
    description = (f"{draft.release.changelog.strip()}\n\n---\n由 ToolManager 發布精靈建立"
                   f"（{publisher.current_user()}）")
    url, handle = publisher.open_pull_request(branch, title, description)
    if not publisher.uses_pull_requests:
        return SubmitResult(url, merged=True)
    if draft.is_new:
        return SubmitResult(url, merged=False, waiting_reason="new_tool")
    say("merge")
    try:
        publisher.merge_pull_request(handle)
    except MergeBlocked as e:
        return SubmitResult(url, merged=False, waiting_reason=str(e))
    return SubmitResult(url, merged=True)

