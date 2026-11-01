"""Per-user preferences kept next to the settings: favorite tools and recently used tools."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

MAX_RECENT = 10


class Prefs:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.favorites: set[str] = set()
        # tool id -> last use (epoch seconds)
        self.recent: dict[str, float] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            self.favorites = {str(t) for t in data.get("favorites", [])}
            self.recent = {str(k): float(v) for k, v in data.get("recent", {}).items()}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError) as e:
            log.warning("ignoring unreadable %s: %s", self.path, e)  # start fresh, never crash

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"favorites": sorted(self.favorites),
                                   "recent": self.recent}), "utf-8")
        os.replace(tmp, self.path)

    def is_favorite(self, tool_id: str) -> bool:
        return tool_id in self.favorites

    def toggle_favorite(self, tool_id: str) -> bool:
        """Returns the new state."""
        with self._lock:
            if tool_id in self.favorites:
                self.favorites.discard(tool_id)
            else:
                self.favorites.add(tool_id)
            self._save()
            return tool_id in self.favorites

    def record_use(self, tool_id: str, when: float | None = None) -> None:
        with self._lock:
            self.recent[tool_id] = time.time() if when is None else when
            newest = sorted(self.recent.items(), key=lambda kv: kv[1], reverse=True)
            self.recent = dict(newest[:MAX_RECENT])
            self._save()

    def recent_ids(self) -> list[str]:
        """Most recent first."""
        return [t for t, _ in sorted(self.recent.items(), key=lambda kv: kv[1], reverse=True)]
