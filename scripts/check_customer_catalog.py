"""Check a customer catalog before it is pushed (see docs/CUSTOMER.md).

Run it on a local checkout of the customer catalog repo (with `git lfs pull` done, so
the tool zips can be inspected):

    uv run python scripts/check_customer_catalog.py <customer catalog folder> \
        [--deny internal-keywords.txt]

--deny: one regular expression per line ("#" starts a comment), matched case-insensitively
against everything a customer can read: tool names, summaries, READMEs, release notes,
links, form labels and the file names inside each package. Typical entries: internal host
names, project code names, internal tool names.

Exit code 0 = no errors (warnings may remain), 1 = errors found.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from toolmanager.core.lfs import parse_pointer
from toolmanager.core.manifest import CATALOG_FILE, ManifestError, load_catalog
from toolmanager.core.models import Tool, ToolType


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, text: str) -> None:
        self.errors.append(text)

    def warn(self, text: str) -> None:
        self.warnings.append(text)


def load_patterns(path: Path | None) -> list[re.Pattern]:
    if path is None:
        return []
    patterns = []
    for line in path.read_text("utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            patterns.append(re.compile(line, re.IGNORECASE))
    return patterns


def _visible_texts(tool: Tool) -> list[tuple[str, str]]:
    """(where, text) pairs a customer can read in ToolManager."""
    texts = [("名稱", tool.name), ("摘要", tool.summary), ("README", tool.readme)]
    if tool.issue_url:
        texts.append(("issue_url", tool.issue_url))
    for rel in tool.releases:
        where = f"{rel.version} "
        texts += [(where + "更新說明", rel.changelog), (where + "重要說明", rel.important_note)]
        for arg in rel.args:
            texts += [(where + f"參數 {arg.name}", f"{arg.label}\n{arg.help}")]
    return texts


def _zip_names(path: Path, report: Report, label: str) -> list[str] | None:
    head = path.read_bytes()[:1024]
    if parse_pointer(head) is not None:
        report.warn(f"{label}：只有 Git LFS 指標檔，無法檢查內容（請先執行 git lfs pull）")
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.namelist()
    except zipfile.BadZipFile:
        report.error(f"{label}：不是有效的 zip")
        return None


def _strip_single_root(names: list[str]) -> list[str]:
    roots = {n.split("/", 1)[0] for n in names if n}
    if len(roots) == 1 and all("/" in n for n in names if n):
        return [n.split("/", 1)[1] for n in names]  # zipped as MyTool/...
    return names


def check(folder: Path, patterns: list[re.Pattern]) -> Report:
    report = Report()
    try:
        result = load_catalog(folder)
    except ManifestError as e:
        report.error(f"catalog.toml 無法讀取：{e}")
        return report
    for problem in result.problems:
        report.error(f"設定檔錯誤：{problem}")

    raw = tomllib.loads((folder / CATALOG_FILE).read_text("utf-8"))
    if "jira" in raw:
        report.error("catalog.toml 含有 [jira]：客戶無法使用內部 Jira，請移除")
    if raw.get("python_offline_only") is not True:
        misplaced = any(isinstance(c, dict) and "python_offline_only" in c
                        for c in raw.get("categories", []))
        report.error("catalog.toml 必須設定 python_offline_only = true"
                     "（客戶端連不到內部 PyPI mirror）"
                     + ("；它目前寫在 [[categories]] 底下，請移到檔案開頭、第一個 [ ] 之前"
                        if misplaced else ""))

    def scan(where: str, text: str) -> None:
        for pattern in patterns:
            m = pattern.search(text or "")
            if m:
                report.error(f"{where}：包含內部關鍵字「{m.group(0)}」")

    for tool in result.catalog.tools:
        for where, text in _visible_texts(tool):
            scan(f"{tool.id} {where}", text)
        if tool.issue_url:
            report.warn(f"{tool.id}：設有 issue_url（{tool.issue_url}），請確認客戶能開啟")
        for rel in tool.releases:
            label = f"{tool.id} {rel.version}"
            artifact = folder / rel.artifact
            if not artifact.is_file():
                report.error(f"{label}：找不到 {rel.artifact}")
                continue
            names = _zip_names(artifact, report, label)
            if names is None:
                continue
            for name in names:
                scan(f"{label} 套件內檔名", name)
            files = _strip_single_root(names)
            if tool.type is ToolType.PYTHON and "requirements.txt" in files and not any(
                n.startswith("wheels/") and n.endswith(".whl") for n in files
            ):
                report.error(f"{label}：有 requirements.txt 但沒有 wheels/*.whl，"
                             "客戶端無法安裝套件")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a customer catalog before pushing it.")
    parser.add_argument("catalog", type=Path, help="local checkout of the customer catalog repo")
    parser.add_argument("--deny", type=Path, help="file with internal keywords (regex per line)")
    args = parser.parse_args(argv)

    report = check(args.catalog, load_patterns(args.deny))
    for text in report.errors:
        print(f"[錯誤] {text}")
    for text in report.warnings:
        print(f"[注意] {text}")
    print(f"\n{len(report.errors)} 個錯誤，{len(report.warnings)} 個注意事項")
    if not args.deny:
        print("（未提供 --deny：沒有檢查內部關鍵字）")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
