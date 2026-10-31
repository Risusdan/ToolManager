"""Checks used by the setup wizard and settings page before anything is saved."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..backends.bitbucket_server import BitbucketServerRepository
from .settings import BitbucketSettings


@dataclass(frozen=True)
class ConnectionInfo:
    username: str | None
    revision: str


def check_connection(
    bb: BitbucketSettings, token: str, client: httpx.Client | None = None
) -> ConnectionInfo:
    """Read the catalog branch with this token. Raises RepositoryError (with .kind) on failure."""
    repo = BitbucketServerRepository(bb.base_url, bb.project, bb.repo, token, bb.branch, client)
    revision = repo.latest_revision()
    return ConnectionInfo(username=repo.username, revision=revision)


def token_page_url(base_url: str) -> str:
    """Bitbucket Data Center page where users create personal HTTP access tokens."""
    return f"{base_url.rstrip('/')}/plugins/servlet/access-tokens/manage"
