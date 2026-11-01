"""Checks for a catalog change before it is merged (docs/DESIGN.md §8.3).

Shared by scripts/check_catalog.py (run on a pull request) and the publish wizard (run before
sending). Messages are for the people publishing and reviewing tools, in Chinese like the
rest of the department-facing text.

With a base catalog (the target branch) the change itself is checked too: published
releases are immutable, a new release must be newer than every existing one, and only a
tool's owners publish new versions of it.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .lfs import parse_pointer
from .manifest import CATALOG_FILE, RELEASE_FILE, ManifestError, load_catalog
from .models import Catalog, Release, Tool, ToolType

MAX_IMAGE_BYTES = 2 * 1024 * 1024
# ![alt](path "title"): relative paths only (README images are files in the tool folder).
IMAGE_RE = re.compile(r"!\[[^\]]*\]\((?!https?:|data:)<?([^)\s>]+)>?")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # For the reviewer: what kind of change this is (new tool, admin-only change, ...).
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def file_digest(path: Path) -> str:
    """sha256 of the real content: an LFS pointer stands for the file it points to."""
    data = path.read_bytes()
    pointer = parse_pointer(data[:1024]) if len(data) <= 1024 else None
    return pointer.oid if pointer else hashlib.sha256(data).hexdigest()


def zip_names(path: Path) -> list[str] | None:
    """Entry names with a single top folder removed (installs flatten it the same way);
    None for an LFS pointer, whose content cannot be inspected without `git lfs pull`."""
    with path.open("rb") as f:
        if parse_pointer(f.read(1024)) is not None:
            return None
    with zipfile.ZipFile(path) as zf:
        return strip_single_root([n for n in zf.namelist() if not n.endswith("/")])


def strip_single_root(names: list[str]) -> list[str]:
    """Authors often zip "MyTool/" instead of its contents; installs flatten that folder
    (archive.flatten_single_root), so entries are named without it."""
    roots = {n.split("/", 1)[0] for n in names}
    if len(roots) == 1 and all("/" in n for n in names):
        return [n.split("/", 1)[1] for n in names]
    return names


def same_account(a: str, b: str) -> bool:
    """Bitbucket user names are case-insensitive."""
    return a.casefold() == b.casefold()


def is_one_of(account: str, accounts) -> bool:
    return any(same_account(account, a) for a in accounts)


def check_release_files(folder: Path, tool: Tool, release: Release, report: Report,
                        lfs_tracked: Callable[[str], bool | None] | None = None) -> None:
    """The package of one release: present, a valid zip with its entry, stored in LFS."""
    label = f"{tool.id} {release.version}"
    artifact = folder / release.artifact
    if not artifact.is_file():
        report.errors.append(f"{label}：找不到套件 {release.artifact}")
        return
    try:
        names = zip_names(artifact)
    except zipfile.BadZipFile:
        report.errors.append(f"{label}：{release.artifact} 不是有效的 zip")
        return
    if names is None:
        report.warnings.append(f"{label}：套件只有 Git LFS 指標檔，未檢查內容"
                               "（可先執行 git lfs pull）")
    else:
        entry = release.entry.replace("\\", "/")
        if entry not in names:
            report.errors.append(f"{label}：套件內找不到 entry「{release.entry}」")
        if tool.type is ToolType.PYTHON and not entry.endswith((".py", ".pyw")):
            report.errors.append(f"{label}：Python 工具的 entry 必須是 .py 或 .pyw")
    if lfs_tracked is not None and lfs_tracked(release.artifact) is False:
        report.errors.append(f"{label}：{release.artifact} 沒有用 Git LFS 儲存"
                             "（catalog repo 的 .gitattributes 需追蹤 tools/*/releases/*/*.zip）")


def check_readme_images(tool_dir: Path, tool: Tool, report: Report) -> None:
    base = tool_dir.resolve()
    for ref in IMAGE_RE.findall(tool.readme):
        ref = ref.split("#", 1)[0].split("?", 1)[0]
        target = (tool_dir / ref.removeprefix("./")).resolve()
        if ref.startswith("/") or not target.is_relative_to(base):
            report.errors.append(f"{tool.id} README：圖片 {ref} 必須放在工具資料夾內")
        elif not target.is_file():
            report.errors.append(f"{tool.id} README：找不到圖片 {ref}")
        elif target.stat().st_size > MAX_IMAGE_BYTES:
            size = target.stat().st_size / 1024 / 1024
            report.errors.append(f"{tool.id} README：圖片 {ref} 為 {size:.1f} MB，上限 2 MB")


def check_catalog(folder: Path, base: Path | None = None, author: str = "",
                  lfs_tracked: Callable[[str], bool | None] | None = None) -> Report:
    """Check a catalog folder; with base (the target branch's catalog) check the change too.

    lfs_tracked(path) says whether git stores that path in LFS (None: unknown).
    """
    report = Report()
    try:
        result = load_catalog(folder)
    except ManifestError as e:
        report.errors.append(f"catalog.toml 無法讀取：{e}")
        return report
    for problem in result.problems:
        report.errors.append(f"設定檔錯誤：{problem}")
    catalog = result.catalog
    for tool in catalog.tools:
        if not tool.releases:
            report.errors.append(f"{tool.id}：沒有任何版本（releases/<版本>/release.toml）")
        for release in tool.releases:
            check_release_files(folder, tool, release, report, lfs_tracked)
        check_readme_images(folder / "tools" / tool.id, tool, report)

    if base is not None:
        _check_change(folder, catalog, base, author, report)
    return report


def check_publish(staged: Path, base: Path, tool_id: str, version: str, author: str) -> Report:
    """The publish wizard's check: `staged` is the synced catalog (manifests only, no zips)
    with the new release written into it, `base` the synced catalog itself. Only the tool
    being published is checked for files; the change rules are the same as on merge."""
    report = Report()
    try:
        result = load_catalog(staged)
    except ManifestError as e:
        report.errors.append(f"catalog.toml 無法讀取：{e}")
        return report
    tool_dir = staged / "tools" / tool_id
    for problem in result.problems:
        if Path(problem.path).is_relative_to(tool_dir):
            report.errors.append(f"設定檔錯誤：{problem.message}（{Path(problem.path).name}）")
    tool = result.catalog.tool(tool_id)
    if tool is None:
        return report
    release = next((r for r in tool.releases if str(r.version) == version), None)
    if release is not None:
        check_release_files(staged, tool, release, report)
    check_readme_images(staged / "tools" / tool_id, tool, report)
    _check_change(staged, result.catalog, base, author, report)
    return report


def _check_change(folder: Path, catalog: Catalog, base_folder: Path, author: str,
                  report: Report) -> None:
    try:
        base = load_catalog(base_folder).catalog
    except ManifestError as e:
        report.warnings.append(f"目標分支的 catalog 無法讀取，略過變更檢查：{e}")
        return
    if (folder / CATALOG_FILE).read_bytes() != (base_folder / CATALOG_FILE).read_bytes():
        report.notes.append("catalog.toml 有變更：需由管理員審核")
        if author and not is_one_of(author, base.admins):
            report.errors.append(f"{author} 不是管理員，不能修改 catalog.toml")

    current_ids = {t.id for t in catalog.tools}
    for old in base.tools:
        if old.id not in current_ids:
            report.warnings.append(f"{old.id}：整個工具被移除，請確認是刻意的")

    for tool in catalog.tools:
        old = base.tool(tool.id)
        if old is None:
            report.notes.append(f"{tool.id}：新工具，需管理員核准")
            continue
        if not _tool_changed(folder, base_folder, tool.id):
            continue
        if author and not is_one_of(author, old.owners) and not is_one_of(author, base.admins):
            report.errors.append(f"{tool.id}：{author} 不在 owners（{', '.join(old.owners)}）"
                                 "，不能發布此工具")
        if set(tool.owners) != set(old.owners):
            report.notes.append(f"{tool.id}：owners 變更為 {', '.join(tool.owners)}")
        old_versions = {r.version for r in old.releases}
        for release in old.releases:
            if release.version not in {r.version for r in tool.releases}:
                report.warnings.append(f"{tool.id} {release.version}：已發布的版本被移除，"
                                       "已安裝的同仁將無法回退到這個版本")
                continue
            _check_unchanged(folder, base_folder, tool.id, release, report)
        newest_old = max(old_versions, default=None)
        for release in tool.releases:
            if release.version in old_versions:
                continue
            if newest_old is not None and release.version <= newest_old:
                report.errors.append(f"{tool.id} {release.version}：新版本號必須大於目前最新的 "
                                     f"{newest_old}")
            else:
                report.notes.append(f"{tool.id}：新版本 {release.version}")


def _tool_changed(folder: Path, base_folder: Path, tool_id: str) -> bool:
    def files(root: Path) -> dict[str, str]:
        tool_dir = root / "tools" / tool_id
        return {p.relative_to(tool_dir).as_posix(): file_digest(p)
                for p in tool_dir.rglob("*") if p.is_file()}

    return files(folder) != files(base_folder)


def _check_unchanged(folder: Path, base_folder: Path, tool_id: str, release: Release,
                     report: Report) -> None:
    rel_dir = f"tools/{tool_id}/releases/{release.version}"
    for rel in (f"{rel_dir}/{RELEASE_FILE}", release.artifact):
        new, old = folder / rel, base_folder / rel
        if new.is_file() and old.is_file() and file_digest(new) != file_digest(old):
            report.errors.append(f"{tool_id} {release.version}：已發布的版本不可修改（{rel}），"
                                 "請改發布新版本")

