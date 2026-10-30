"""Personal access token storage in the OS credential store (Windows Credential Manager)."""

from __future__ import annotations

import keyring
import keyring.errors

SERVICE = "ToolManager"


def _key(base_url: str) -> str:
    return f"{SERVICE}:{base_url.rstrip('/')}"


def get_token(base_url: str) -> str | None:
    return keyring.get_password(_key(base_url), "token")


def set_token(base_url: str, token: str) -> None:
    keyring.set_password(_key(base_url), "token", token)


def delete_token(base_url: str) -> None:
    try:
        keyring.delete_password(_key(base_url), "token")
    except keyring.errors.PasswordDeleteError:
        pass
