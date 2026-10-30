"""Repository implementations."""

from __future__ import annotations

from pathlib import Path

from ..core.credentials import get_token
from ..core.repository import Repository, RepositoryError
from ..core.settings import Settings


def create_repository(settings: Settings) -> Repository:
    if settings.backend == "local":
        from .local import LocalRepository

        if not settings.local_catalog:
            raise RepositoryError("settings.local_catalog is not set")
        return LocalRepository(Path(settings.local_catalog))
    if settings.backend == "bitbucket":
        from .bitbucket_server import BitbucketServerRepository

        bb = settings.bitbucket
        token = get_token(bb.base_url)
        if not token:
            raise RepositoryError("no access token configured")
        return BitbucketServerRepository(bb.base_url, bb.project, bb.repo, token, bb.branch)
    raise RepositoryError(f"unknown backend {settings.backend!r}")
