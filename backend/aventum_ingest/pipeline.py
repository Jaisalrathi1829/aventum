"""
Ingestion pipeline orchestration (Day 2A §1-§13).

Flow:
    integrity -> schema drift -> read-only extraction -> normalization -> validation
    -> quarantine -> staging load -> staging verification -> ATOMIC promotion
    -> post-load verification -> ingestion audit record

Atomicity guarantee: `transactions` is only ever touched inside a single transaction
that deletes the prior rows for this source_dataset and inserts the new set. Any
failure before or during that transaction leaves the authoritative table byte-identical
to its pre-run state -- there is no window in which half the rows are visible.
"""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import Engine, text

from . import __version__ as CODE_VERSION
from .config import Config
from .constants import (
    AUDITED_SOURCE_SHA256,
    SCHEMA_VERSION,
    TIMESTAMP_ASSUMPTION_NOTE,
)
from .dataset_registry import DatasetIdentity, UnknownDatasetError, resolve_identity
from .integrity import SourceFingerprint, fingerprint_source
from .normalize import CANONICAL_COLUMNS, NormalizationError, normalize_row
from .source_schema import (
    SchemaDriftReport,
    assert_schema_compatible,
    read_source_header,
)
from .validate import ErrorCategory, ValidationFailure, find_duplicate_ids, validate_record
from .verify import VerificationReport, verify_canonical_load


class IngestionError(RuntimeError):
    """Ingestion aborted. The canonical table is unchanged."""


class RunStatus:
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED_IDEMPOTENT = "SKIPPED_IDEMPOTENT"


@dataclass
class RejectedRecord:
    source_row_index: int
    transaction_id: str | None
    validation_error: str
    error_category: str
    offending_fields: list[str]
    raw_record: dict[str, Any]


@dataclass
class IngestionResult:
    ingestion_run_id: int | None
    status: str
    source: SourceFingerprint | None
    identity: DatasetIdentity | None = None
    schema_drift: SchemaDriftReport | None = None
    rows_read: int = 0
    rows_valid: int = 0
    rows_rejected: int = 0
    rows_inserted: int = 0
    duration_seconds: float = 0.0
    verification: VerificationReport | None = None
    canonical_fingerprint: str = ""
    reject_categories: dict[str, int] = field(default_factory=dict)
    message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status in (RunStatus.SUCCEEDED, RunStatus.SKIPPED_IDEMPOTENT)


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def iter_source_rows(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """
    Stream the source read-only, yielding (source_row_index, raw_row).

    csv.DictReader is used rather than pandas so that (a) values arrive as plain
    strings without dtype inference guessing at them, and (b) memory stays flat
    regardless of file size. source_row_index is 0-based over data rows (header
    excluded), which is what the quarantine records point at.
    """
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames:
            reader.fieldnames = [name.strip() for name in reader.fieldnames]
        for index, row in enumerate(reader):
            yield index, row


# --------------------------------------------------------------------------
# Transform + validate
# --------------------------------------------------------------------------

def _normalize_and_validate(
    path: Path,
    source_dataset: str,
) -> tuple[list[dict[str, Any]], list[RejectedRecord], int]:
    """
    Normalize and validate every source row.

    `source_dataset` comes from the caller's resolved dataset identity, so every
    canonical record carries provenance that was verified against the file's content
    hash rather than assumed (P1-1).

    Returns (valid_records, rejected_records, rows_read). Rows are processed in source
    order and valid records preserve that order, keeping the run deterministic.
    """
    valid: list[dict[str, Any]] = []
    rejected: list[RejectedRecord] = []
    rows_read = 0

    for source_row_index, raw in iter_source_rows(path):
        rows_read += 1

        try:
            record = normalize_row(raw, source_row_index, source_dataset)
        except NormalizationError as exc:
            rejected.append(
                RejectedRecord(
                    source_row_index=source_row_index,
                    transaction_id=(raw.get("transaction id") or None),
                    validation_error=str(exc),
                    error_category=ErrorCategory.NORMALIZATION,
                    offending_fields=[exc.field],
                    raw_record=raw,
                )
            )
            continue

        failures: list[ValidationFailure] = validate_record(record)
        if failures:
            rejected.append(
                RejectedRecord(
                    source_row_index=source_row_index,
                    transaction_id=record.get("transaction_id"),
                    validation_error="; ".join(f.message for f in failures),
                    error_category=failures[0].category,
                    offending_fields=sorted({f for fail in failures for f in fail.fields}),
                    raw_record=raw,
                )
            )
            continue

        valid.append(record)

    # Uniqueness is a dataset-level property, so it is checked after per-record
    # validation. Every row carrying a duplicated id is quarantined -- including the
    # first occurrence, since we cannot know which one is authoritative.
    duplicates = find_duplicate_ids(valid)
    if duplicates:
        kept: list[dict[str, Any]] = []
        for record in valid:
            txn_id = record["transaction_id"]
            if txn_id in duplicates:
                rejected.append(
                    RejectedRecord(
                        source_row_index=record["source_row_index"],
                        transaction_id=txn_id,
                        validation_error=(
                            f"transaction_id {txn_id!r} appears {duplicates[txn_id]} times in "
                            "the source; canonical identity must be unique"
                        ),
                        error_category=ErrorCategory.DUPLICATE_ID,
                        offending_fields=["transaction_id"],
                        raw_record={"transaction_id": txn_id},
                    )
                )
            else:
                kept.append(record)
        valid = kept

    rejected.sort(key=lambda r: r.source_row_index)
    return valid, rejected, rows_read


# --------------------------------------------------------------------------
# Load
# --------------------------------------------------------------------------

def _copy_records_to_staging(connection, records: list[dict[str, Any]]) -> int:
    """
    Bulk-load valid records into staging via PostgreSQL COPY.

    COPY is used instead of per-row INSERT so a 250k-row load stays in the
    seconds range and the same code path scales to millions of rows.
    """
    raw_connection = connection.connection  # psycopg3 connection
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    for record in records:
        writer.writerow(
            [
                record["transaction_id"],
                record["timestamp"].isoformat(),
                str(record["amount"]),
                record["status"],
                record["payment_method"],
                "" if record["merchant_category"] is None else record["merchant_category"],
                record["region"],
                record["device"],
                record["network"],
                record["sender_bank"],
                record["receiver_bank"],
                "true" if record["fraud_flag"] else "false",
                record["source_dataset"],
                record["source_row_index"],
            ]
        )
    buffer.seek(0)

    columns = ", ".join(CANONICAL_COLUMNS)
    copy_sql = (
        f"COPY transactions_staging ({columns}) "
        "FROM STDIN WITH (FORMAT csv, NULL '')"
    )
    with raw_connection.cursor() as cursor:
        with cursor.copy(copy_sql) as copy:
            copy.write(buffer.getvalue())

    return len(records)


def _verify_staging(connection, expected_rows: int) -> None:
    """Verify staging before it is allowed to touch the authoritative table."""
    staged = connection.execute(text("SELECT COUNT(*) FROM transactions_staging")).scalar_one()
    if staged != expected_rows:
        raise IngestionError(
            f"Staging verification failed: expected {expected_rows} rows, found {staged}. "
            "Canonical table left unchanged."
        )

    distinct_ids = connection.execute(
        text("SELECT COUNT(DISTINCT transaction_id) FROM transactions_staging")
    ).scalar_one()
    if distinct_ids != expected_rows:
        raise IngestionError(
            f"Staging verification failed: {expected_rows} rows but only {distinct_ids} distinct "
            "transaction_ids. Canonical table left unchanged."
        )

    # Referential integrity is checked here rather than being discovered at promotion,
    # so the failure message names the actual problem.
    orphan_banks = connection.execute(
        text(
            """
            SELECT COUNT(*) FROM transactions_staging s
            WHERE NOT EXISTS (SELECT 1 FROM banks b WHERE b.bank_code = s.sender_bank)
               OR NOT EXISTS (SELECT 1 FROM banks b WHERE b.bank_code = s.receiver_bank)
            """
        )
    ).scalar_one()
    if orphan_banks:
        raise IngestionError(
            f"Staging verification failed: {orphan_banks} rows reference a bank_code absent from "
            "the banks dimension. Canonical table left unchanged."
        )


# --------------------------------------------------------------------------
# Audit record helpers
# --------------------------------------------------------------------------

def _open_run(
    connection,
    source: SourceFingerprint,
    drift: SchemaDriftReport,
    identity: DatasetIdentity,
) -> int:
    """Open the audit run, recording the RESOLVED dataset identity as provenance."""
    return connection.execute(
        text(
            """
            INSERT INTO ingestion_runs (
                source_file, source_filename, source_sha256, source_size_bytes,
                source_dataset, schema_version, code_version, status,
                schema_drift_report, timestamp_assumption
            ) VALUES (
                :source_file, :source_filename, :sha256, :size_bytes,
                :dataset, :schema_version, :code_version, :status,
                CAST(:drift AS jsonb), :ts_assumption
            )
            RETURNING ingestion_run_id
            """
        ),
        {
            "source_file": source.display_path,
            "source_filename": source.filename,
            "sha256": source.sha256,
            "size_bytes": source.size_bytes,
            "dataset": identity.dataset_name,
            "schema_version": SCHEMA_VERSION,
            "code_version": CODE_VERSION,
            "status": RunStatus.RUNNING,
            "drift": json.dumps(drift.to_dict()),
            "ts_assumption": TIMESTAMP_ASSUMPTION_NOTE,
        },
    ).scalar_one()


def _close_run(
    connection,
    run_id: int,
    status: str,
    started: float,
    rows_read: int,
    rows_valid: int,
    rows_rejected: int,
    rows_inserted: int,
    fingerprint: str | None = None,
    verification: dict | None = None,
    error_message: str | None = None,
) -> float:
    duration = time.perf_counter() - started
    connection.execute(
        text(
            """
            UPDATE ingestion_runs SET
                status = :status,
                finished_at = :finished_at,
                duration_seconds = :duration,
                rows_read = :rows_read,
                rows_valid = :rows_valid,
                rows_rejected = :rows_rejected,
                rows_inserted = :rows_inserted,
                canonical_fingerprint = :fingerprint,
                verification_report = CAST(:verification AS jsonb),
                error_message = :error_message
            WHERE ingestion_run_id = :run_id
            """
        ),
        {
            "status": status,
            # Wall-clock time is used ONLY for audit metadata, never as a
            # transformation input (Day 2A §4).
            "finished_at": datetime.now(timezone.utc),
            "duration": round(duration, 3),
            "rows_read": rows_read,
            "rows_valid": rows_valid,
            "rows_rejected": rows_rejected,
            "rows_inserted": rows_inserted,
            "fingerprint": fingerprint,
            "verification": json.dumps(verification) if verification is not None else None,
            "error_message": error_message,
            "run_id": run_id,
        },
    )
    return duration


def _persist_rejects(connection, run_id: int, rejects: list[RejectedRecord]) -> None:
    if not rejects:
        return
    connection.execute(
        text(
            """
            INSERT INTO ingestion_rejects (
                ingestion_run_id, source_row_index, transaction_id,
                validation_error, error_category, offending_fields, raw_record
            ) VALUES (
                :run_id, :row_index, :txn_id,
                :error, :category, CAST(:fields AS jsonb), CAST(:raw AS jsonb)
            )
            """
        ),
        [
            {
                "run_id": run_id,
                "row_index": reject.source_row_index,
                "txn_id": reject.transaction_id,
                "error": reject.validation_error,
                "category": reject.error_category,
                "fields": json.dumps(reject.offending_fields),
                "raw": json.dumps(reject.raw_record, default=str),
            }
            for reject in rejects
        ],
    )


def _find_prior_successful_run(connection, sha256: str) -> dict | None:
    """Locate a completed run over the identical source bytes + contract version."""
    row = connection.execute(
        text(
            """
            SELECT ingestion_run_id, rows_inserted, canonical_fingerprint
            FROM ingestion_runs
            WHERE source_sha256 = :sha
              AND schema_version = :schema_version
              AND code_version = :code_version
              AND status = :status
            ORDER BY ingestion_run_id DESC
            LIMIT 1
            """
        ),
        {
            "sha": sha256,
            "schema_version": SCHEMA_VERSION,
            "code_version": CODE_VERSION,
            "status": RunStatus.SUCCEEDED,
        },
    ).mappings().first()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run_ingestion(
    engine: Engine,
    config: Config,
    force: bool = False,
) -> IngestionResult:
    """
    Execute the full ingestion.

    Idempotency (Day 2A §11): if a SUCCEEDED run already exists for the identical
    source bytes, schema version, and code version, the run is recorded as
    SKIPPED_IDEMPOTENT and the canonical table is left exactly as it is. `force=True`
    re-executes; because promotion is delete-then-insert scoped to source_dataset
    inside one transaction, a forced re-run converges on identical canonical content
    rather than duplicating it.
    """
    started = time.perf_counter()

    # ---- 1. Source integrity (before any DB work) ----
    source = fingerprint_source(config.source_path, display_path=config.source_display_path)

    # ---- 2. Schema drift (before any canonical mutation) ----
    drift = assert_schema_compatible(source.path)

    # ---- 3. Trusted dataset identity (before any canonical mutation) ----
    # Resolves source_dataset from the file's CONTENT HASH via the registry. An
    # unregistered file raises UnknownDatasetError here -- before a run row is opened
    # and long before any DELETE -- so it can neither be labelled as a known dataset
    # nor displace one (P1-1).
    with engine.begin() as connection:
        identity = resolve_identity(connection, source, expected_schema_version=SCHEMA_VERSION)

    # ---- 4. Idempotency gate ----
    with engine.begin() as connection:
        prior = _find_prior_successful_run(connection, source.sha256)

    if prior and not force:
        with engine.begin() as connection:
            run_id = _open_run(connection, source, drift, identity)
            duration = _close_run(
                connection,
                run_id,
                RunStatus.SKIPPED_IDEMPOTENT,
                started,
                rows_read=0,
                rows_valid=0,
                rows_rejected=0,
                rows_inserted=0,
                fingerprint=prior["canonical_fingerprint"],
                verification=None,
                error_message=None,
            )
            connection.execute(
                text("UPDATE ingestion_runs SET notes = :notes WHERE ingestion_run_id = :rid"),
                {
                    "notes": (
                        f"Skipped: identical source (sha256={source.sha256}) already ingested "
                        f"by run {prior['ingestion_run_id']} under the same schema_version "
                        f"{SCHEMA_VERSION} and code_version {CODE_VERSION}. "
                        "Canonical table left unchanged. Use --force to re-ingest."
                    ),
                    "rid": run_id,
                },
            )
        return IngestionResult(
            ingestion_run_id=run_id,
            status=RunStatus.SKIPPED_IDEMPOTENT,
            source=source,
            identity=identity,
            schema_drift=drift,
            duration_seconds=time.perf_counter() - started,
            canonical_fingerprint=prior["canonical_fingerprint"] or "",
            message=(
                f"Source already ingested by run {prior['ingestion_run_id']}; "
                "nothing changed (idempotent skip)."
            ),
        )

    # ---- 5. Open the audit run ----
    with engine.begin() as connection:
        run_id = _open_run(connection, source, drift, identity)

    try:
        # ---- 6. Extract -> normalize -> validate ----
        valid_records, rejects, rows_read = _normalize_and_validate(
            source.path, identity.dataset_name
        )

        # ---- 6. Quarantine (committed independently so it survives a later failure) ----
        with engine.begin() as connection:
            _persist_rejects(connection, run_id, rejects)

        # ---- 7. Stage -> verify staging -> ATOMIC promotion ----
        # Everything below happens in ONE transaction. If any statement raises, the
        # whole thing rolls back and `transactions` keeps its pre-run contents.
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE transactions_staging"))
            _copy_records_to_staging(connection, valid_records)
            _verify_staging(connection, len(valid_records))

            # Promotion: replace THIS RESOLVED DATASET's canonical rows wholesale.
            # Scoping the delete to the resolved identity is what stops one dataset from
            # displacing another: ingesting dataset B can only ever remove B's own rows.
            connection.execute(
                text("DELETE FROM transactions WHERE source_dataset = :ds"),
                {"ds": identity.dataset_name},
            )
            inserted = connection.execute(
                text(
                    """
                    INSERT INTO transactions (
                        transaction_id, timestamp, amount, status, payment_method,
                        merchant_category, region, device, network,
                        sender_bank, receiver_bank, fraud_flag,
                        source_dataset, ingestion_run_id
                    )
                    SELECT
                        transaction_id, timestamp, amount, status, payment_method,
                        merchant_category, region, device, network,
                        sender_bank, receiver_bank, fraud_flag,
                        source_dataset, :run_id
                    FROM transactions_staging
                    ORDER BY source_row_index
                    """
                ),
                {"run_id": run_id},
            ).rowcount

            # Staging is cleared inside the same transaction: no leftover partial state.
            connection.execute(text("TRUNCATE TABLE transactions_staging"))

        # ---- 9. Post-load verification (independent re-query) ----
        # The Day 1 distribution expectations describe one specific file, so they are
        # asserted exactly when the ingested bytes are that file, and recorded as
        # explicitly skipped otherwise -- never silently treated as passing.
        verification = verify_canonical_load(
            engine,
            ingestion_run_id=run_id,
            expect_dataset_invariants=(source.sha256 == AUDITED_SOURCE_SHA256),
            source_dataset=identity.dataset_name,
        )

        if not verification.passed:
            with engine.begin() as connection:
                _close_run(
                    connection, run_id, RunStatus.FAILED, started,
                    rows_read, len(valid_records), len(rejects), inserted,
                    fingerprint=verification.canonical_fingerprint,
                    verification=verification.to_dict(),
                    error_message=verification.summary(),
                )
            raise IngestionError(
                "Post-load verification FAILED after promotion.\n" + verification.summary()
            )

        # ---- 9. Close the audit record ----
        with engine.begin() as connection:
            duration = _close_run(
                connection, run_id, RunStatus.SUCCEEDED, started,
                rows_read, len(valid_records), len(rejects), inserted,
                fingerprint=verification.canonical_fingerprint,
                verification=verification.to_dict(),
            )

        categories: dict[str, int] = {}
        for reject in rejects:
            categories[reject.error_category] = categories.get(reject.error_category, 0) + 1

        return IngestionResult(
            ingestion_run_id=run_id,
            status=RunStatus.SUCCEEDED,
            source=source,
            identity=identity,
            schema_drift=drift,
            rows_read=rows_read,
            rows_valid=len(valid_records),
            rows_rejected=len(rejects),
            rows_inserted=inserted,
            duration_seconds=duration,
            verification=verification,
            canonical_fingerprint=verification.canonical_fingerprint,
            reject_categories=categories,
            message="Ingestion succeeded.",
        )

    except Exception as exc:
        # Record the failure without masking the original error.
        try:
            with engine.begin() as connection:
                # Row accounting must stay consistent with the CHECK constraint even
                # when the failure happened before counts were known.
                connection.execute(
                    text(
                        """
                        UPDATE ingestion_runs SET
                            status = :status,
                            finished_at = now(),
                            duration_seconds = :duration,
                            error_message = :error
                        WHERE ingestion_run_id = :rid
                        """
                    ),
                    {
                        "status": RunStatus.FAILED,
                        "duration": round(time.perf_counter() - started, 3),
                        "error": f"{type(exc).__name__}: {exc}",
                        "rid": run_id,
                    },
                )
        except Exception:
            pass  # Never let audit bookkeeping replace the real exception.
        raise
