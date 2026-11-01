"""Which update notifications to show (pure logic; the tray shows them).

Each (tool, version) is announced once per run, so the periodic check does not repeat
the same message every few hours.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..i18n import _


@dataclass(frozen=True)
class Update:
    tool_id: str
    name: str
    version: str
    important: bool = False


@dataclass
class UpdateNotifier:
    announced: set[tuple[str, str]] = field(default_factory=set)

    def message(self, updates: list[Update]) -> tuple[str, str] | None:
        """(title, text) for updates not announced yet, or None."""
        new = [u for u in updates if (u.tool_id, u.version) not in self.announced]
        if not new:
            return None
        self.announced.update((u.tool_id, u.version) for u in new)
        important = [u for u in new if u.important]
        if len(new) == 1:
            u = new[0]
            title = _("{name} {version} is available", name=u.name, version=u.version)
        else:
            title = _("{count} tools have new versions", count=len(new))
        names = "、".join(u.name for u in new[:3]) + ("…" if len(new) > 3 else "")
        text = (_("The author recommends updating soon: {names}",
                  names="、".join(u.name for u in important))
                if important else names)
        return title, text
