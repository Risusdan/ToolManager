"""Pure presentation helpers (no NiceGUI), so they can be unit-tested."""

from __future__ import annotations

import re
import time

from packaging.version import Version

from ..core.installer import ToolStatus, UpdateState
from ..core.models import Tool

_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_WORD = re.compile(r"[A-Za-z0-9]+")


def initials(name: str) -> str:
    """Short mark for a tool tile: first CJK character, or up to two Latin initials."""
    name = name.strip()
    if not name:
        return "?"
    if _CJK.match(name):
        return name[0]
    words = _WORD.findall(name)
    if not words:
        return name[0]
    if len(words) == 1:
        return words[0][:2].capitalize()
    return (words[0][0] + words[1][0]).upper()


def strip_title_heading(markdown: str, title: str) -> str:
    """Drop a leading '# Title' that just repeats the tool name shown above it."""
    m = re.match(r"\A\ufeff?\s*#[ \t]+(.+?)[ \t]*#*[ \t]*(?:\r?\n|\Z)", markdown)
    if m and m.group(1).strip().casefold() == title.strip().casefold():
        return markdown[m.end():].lstrip("\r\n")
    return markdown


def sort_key(tool: Tool, status: ToolStatus | None) -> tuple[int, str]:
    """Updates first, then other installed tools, then everything else; by name within."""
    if status is None or status.state is UpdateState.NOT_INSTALLED:
        group = 2
    elif status.state is UpdateState.UPDATE_AVAILABLE:
        group = 0
    else:
        group = 1
    return group, tool.name.casefold()


def is_newer(previous: Version, current: Version) -> bool:
    """True when the "previous" version is newer than the one in use (after a downgrade)."""
    return previous > current


def days_left(deleted_at: float, retention_days: int, now: float | None = None) -> int:
    now = time.time() if now is None else now
    remaining = deleted_at + retention_days * 86400 - now
    return max(0, int((remaining + 86399) // 86400))  # round up: "1 day left" until it is gone


def short_date(ts: float) -> str:
    t = time.localtime(ts)
    return f"{t.tm_mon}/{t.tm_mday}"
