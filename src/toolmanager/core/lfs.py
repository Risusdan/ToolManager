"""Git LFS pointer files (https://github.com/git-lfs/git-lfs/blob/main/docs/spec.md)."""

from __future__ import annotations

from dataclasses import dataclass

POINTER_VERSION = "version https://git-lfs.github.com/spec/v1"
# Real pointers are ~130 bytes; the spec caps them well below this.
MAX_POINTER_SIZE = 1024


@dataclass(frozen=True)
class LfsPointer:
    oid: str  # sha256 hex
    size: int


def parse_pointer(data: bytes) -> LfsPointer | None:
    """Return the pointer if data is an LFS pointer file, otherwise None."""
    if len(data) > MAX_POINTER_SIZE or not data.startswith(POINTER_VERSION.encode()):
        return None
    fields = {}
    for line in data.decode("utf-8", errors="replace").splitlines():
        key, _, value = line.partition(" ")
        fields[key] = value
    oid = fields.get("oid", "")
    size = fields.get("size", "")
    if not oid.startswith("sha256:") or not size.isdigit():
        return None
    return LfsPointer(oid=oid.removeprefix("sha256:"), size=int(size))
