"""
Source integrity: prove exactly which bytes produced the canonical records.

The raw file is opened read-only and never written to. Hashing is streamed so the
same code path works for a 30 MB CSV or a multi-GB file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

_HASH_CHUNK_BYTES = 1024 * 1024  # 1 MiB


class SourceIntegrityError(RuntimeError):
    """Raised when the source file is missing, unreadable, or empty."""


@dataclass(frozen=True)
class SourceFingerprint:
    """Immutable identity of one physical source file at ingestion time."""

    path: Path
    display_path: str
    filename: str
    sha256: str
    size_bytes: int

    def describe(self) -> str:
        return (
            f"{self.display_path} "
            f"(sha256={self.sha256[:16]}..., {self.size_bytes:,} bytes)"
        )


def compute_sha256(path: Path) -> str:
    """Stream a SHA-256 of the file's raw bytes. Read-only, chunked."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_source(path: Path, display_path: str | None = None) -> SourceFingerprint:
    """
    Verify the source exists and is readable, then fingerprint it.

    Raises SourceIntegrityError before any database work occurs, so a missing or
    empty source can never mutate canonical state.
    """
    resolved = Path(path)

    if not resolved.exists():
        raise SourceIntegrityError(f"Source file does not exist: {resolved}")
    if not resolved.is_file():
        raise SourceIntegrityError(f"Source path is not a regular file: {resolved}")

    size_bytes = resolved.stat().st_size
    if size_bytes == 0:
        raise SourceIntegrityError(f"Source file is empty (0 bytes): {resolved}")

    return SourceFingerprint(
        path=resolved,
        display_path=display_path or resolved.as_posix(),
        filename=resolved.name,
        sha256=compute_sha256(resolved),
        size_bytes=size_bytes,
    )
