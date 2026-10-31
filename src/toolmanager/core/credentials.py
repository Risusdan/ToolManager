"""Personal access token storage in the OS credential store (Windows Credential Manager)."""

from __future__ import annotations

import keyring
import keyring.errors

SERVICE = "ToolManager"


class CredentialError(Exception):
    """The OS credential store is unavailable or refused the operation."""


def _key(base_url: str) -> str:
    return f"{SERVICE}:{base_url.rstrip('/')}"


def get_token(base_url: str) -> str | None:
    try:
        return keyring.get_password(_key(base_url), "token")
    except keyring.errors.KeyringError as e:
        raise CredentialError(str(e)) from e


def set_token(base_url: str, token: str) -> None:
    try:
        keyring.set_password(_key(base_url), "token", token)
    except keyring.errors.KeyringError as e:
        raise CredentialError(str(e)) from e


def delete_token(base_url: str) -> None:
    try:
        keyring.delete_password(_key(base_url), "token")
    except keyring.errors.PasswordDeleteError:
        pass
    except keyring.errors.KeyringError as e:
        raise CredentialError(str(e)) from e
