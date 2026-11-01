"""README helpers."""

from __future__ import annotations

import re

# ![alt](path "title") with a relative path (not http(s):, not /, not data:).
_IMG_RE = re.compile(r"(!\[[^\]]*\]\()(?!https?:|/|data:)([^)\s]+)")


def rewrite_relative_images(markdown: str, url_prefix: str) -> str:
    """Point relative image links at url_prefix (assets/a.png -> <prefix>/assets/a.png)."""
    prefix = url_prefix.rstrip("/")
    return _IMG_RE.sub(
        lambda m: f"{m.group(1)}{prefix}/{m.group(2).removeprefix('./')}", markdown
    )
