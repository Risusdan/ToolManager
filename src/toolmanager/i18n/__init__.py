"""Minimal i18n (see docs/DESIGN.md §10.4).

Source strings are English and double as message ids; translations live in
<lang>.toml next to this file. Missing entries fall back to the English text.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_DIR = Path(__file__).parent
_messages: dict[str, str] = {}


def set_language(lang: str) -> None:
    global _messages
    path = _DIR / f"{lang}.toml"
    if path.is_file():
        with path.open("rb") as f:
            _messages = tomllib.load(f).get("messages", {})
    else:
        _messages = {}


def _(text: str, **kwargs: object) -> str:
    translated = _messages.get(text, text)
    return translated.format(**kwargs) if kwargs else translated
