"""
Trusted dataset identity resolution (fixes P1-1).

Provenance model
----------------
A file's canonical dataset identity is established by its CONTENT, never by its name:

    source file  ->  SHA-256  ->  registered dataset identity  ->  source_dataset

`source_dataset` used to be a module constant, so any file routed through `--source`
was labelled `upi_transactions_2024` regardless of what it actually was
(docs/DAY2A_ARCHITECTURE_REVIEW.md, P1-1). Identity now comes from this registry.

Trust boundary
--------------
- Renaming a file does NOT change its identity  (same bytes -> same SHA -> same name).
- Editing a file DOES destroy its identity      (new bytes -> new SHA -> unregistered).

An unregistered SHA-256 is never assigned a dataset name. Ingestion refuses to proceed
rather than guess, so an unknown file can never be labelled as, or overwrite, a known
dataset.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text

from .integrity import SourceFingerprint


class UnknownDatasetError(RuntimeError):
    """
    The source file's SHA-256 is not registered.

    Raised before any canonical mutation. Deliberately not auto-registered: assigning a
    dataset name to unrecognised bytes is exactly the provenance forgery P1-1 describes.
    """


class DatasetRegistrationError(RuntimeError):
    """A registration request conflicts with the existing registry."""


@dataclass(frozen=True)
class DatasetIdentity:
    """A verified binding between file content and a dataset name."""

    dataset_name: str
    source_sha256: str
    schema_version: str
    source_filename: str | None = None
    source_size_bytes: int | None = None
    registered_at: datetime | None = None
    registered_by: str | None = None
    notes: str | None = None

    def describe(self) -> str:
        return (
            f"{self.dataset_name} (sha256={self.source_sha256[:16]}..., "
            f"schema_version={self.schema_version})"
        )


def _row_to_identity(row) -> DatasetIdentity:
    return DatasetIdentity(
        dataset_name=row["dataset_name"],
        source_sha256=row["source_sha256"],
        schema_version=row["schema_version"],
        source_filename=row["source_filename"],
        source_size_bytes=row["source_size_bytes"],
        registered_at=row["registered_at"],
        registered_by=row["registered_by"],
        notes=row["notes"],
    )


def find_by_sha256(connection, sha256: str) -> DatasetIdentity | None:
    """Look up a dataset identity by content hash. Returns None if unregistered."""
    row = (
        connection.execute(
            text("SELECT * FROM dataset_registry WHERE source_sha256 = :sha"),
            {"sha": sha256},
        )
        .mappings()
        .first()
    )
    return _row_to_identity(row) if row else None


def find_by_name(connection, dataset_name: str) -> DatasetIdentity | None:
    """Look up a dataset identity by name (for conflict detection during registration)."""
    row = (
        connection.execute(
            text("SELECT * FROM dataset_registry WHERE dataset_name = :name"),
            {"name": dataset_name},
        )
        .mappings()
        .first()
    )
    return _row_to_identity(row) if row else None


def resolve_identity(
    connection,
    source: SourceFingerprint,
    expected_schema_version: str,
) -> DatasetIdentity:
    """
    Resolve a file's trusted dataset identity, or refuse.

    Raises UnknownDatasetError when the content hash is not registered, and when a
    registered dataset was bound to a different ingestion-contract version than the one
    now running (the source->canonical mapping would differ, so the stored identity no
    longer describes what this code would produce).
    """
    identity = find_by_sha256(connection, source.sha256)

    if identity is None:
        raise UnknownDatasetError(
            f"Source file is not a registered dataset -- ingestion refused BEFORE any "
            f"canonical mutation.\n"
            f"  file      : {source.display_path}\n"
            f"  sha256    : {source.sha256}\n"
            f"  size      : {source.size_bytes:,} bytes\n"
            f"Dataset identity is established by content hash, never by filename, so this "
            f"file cannot be assigned an existing dataset's name.\n"
            f"If this file is genuinely a new dataset, register it deliberately:\n"
            f"    python -m aventum_ingest.cli register --source <path> --name <dataset_name>\n"
            f"Registration only records the identity; it does not load or replace any "
            f"canonical data."
        )

    if identity.schema_version != expected_schema_version:
        raise UnknownDatasetError(
            f"Dataset {identity.dataset_name!r} is registered against ingestion-contract "
            f"schema_version {identity.schema_version!r}, but this code implements "
            f"{expected_schema_version!r}. The source->canonical mapping has changed, so the "
            f"stored identity no longer describes what would be produced. Re-register the "
            f"dataset deliberately if this change is intended."
        )

    return identity


def register_dataset(
    connection,
    source: SourceFingerprint,
    dataset_name: str,
    schema_version: str,
    registered_by: str | None = None,
    notes: str | None = None,
) -> DatasetIdentity:
    """
    Bind a dataset name to this file's content hash.

    Registration establishes identity ONLY. It never loads rows and never replaces
    canonical data -- that remains a separate, explicit `ingest` invocation.

    Refuses to silently rebind either side of an existing pair: one name maps to exactly
    one content hash, and one content hash to exactly one name.
    """
    name = (dataset_name or "").strip()
    if not name:
        raise DatasetRegistrationError("dataset_name must be a non-empty string.")

    existing_by_sha = find_by_sha256(connection, source.sha256)
    if existing_by_sha is not None:
        if existing_by_sha.dataset_name == name:
            return existing_by_sha  # already registered identically; idempotent
        raise DatasetRegistrationError(
            f"This file (sha256={source.sha256}) is already registered as "
            f"{existing_by_sha.dataset_name!r}. One content hash maps to exactly one dataset "
            f"name; refusing to rebind it to {name!r}."
        )

    existing_by_name = find_by_name(connection, name)
    if existing_by_name is not None:
        raise DatasetRegistrationError(
            f"Dataset name {name!r} is already bound to a different file "
            f"(sha256={existing_by_name.source_sha256}). Refusing to rebind a known dataset "
            f"name to new content -- that is the provenance forgery this registry prevents. "
            f"Register the new content under its own name instead."
        )

    connection.execute(
        text(
            """
            INSERT INTO dataset_registry (
                source_sha256, dataset_name, schema_version,
                source_filename, source_size_bytes, registered_by, notes
            ) VALUES (
                :sha, :name, :schema_version, :filename, :size, :by, :notes
            )
            """
        ),
        {
            "sha": source.sha256,
            "name": name,
            "schema_version": schema_version,
            # Filename is stored as METADATA only -- it never participates in identity
            # resolution, which keys exclusively on source_sha256.
            "filename": source.filename,
            "size": source.size_bytes,
            "by": registered_by,
            "notes": notes,
        },
    )

    registered = find_by_sha256(connection, source.sha256)
    assert registered is not None  # just inserted
    return registered


def list_datasets(connection) -> list[DatasetIdentity]:
    rows = (
        connection.execute(
            text("SELECT * FROM dataset_registry ORDER BY dataset_name")
        )
        .mappings()
        .all()
    )
    return [_row_to_identity(row) for row in rows]
