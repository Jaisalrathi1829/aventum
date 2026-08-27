"""
Command-line entrypoints.

    python -m aventum_ingest.cli ingest [--force] [--source PATH]
    python -m aventum_ingest.cli register --source PATH --name DATASET_NAME
    python -m aventum_ingest.cli datasets
    python -m aventum_ingest.cli verify
    python -m aventum_ingest.cli status

`register` and `ingest` are deliberately separate operations: registering a dataset
establishes its identity, it does NOT load rows or replace canonical data.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from .config import load_config
from .constants import SCHEMA_VERSION
from .dataset_registry import (
    DatasetRegistrationError,
    UnknownDatasetError,
    list_datasets,
    register_dataset,
)
from .db import build_engine, database_is_reachable, table_exists
from .integrity import SourceIntegrityError, fingerprint_source
from .pipeline import IngestionError, run_ingestion
from .source_schema import SchemaDriftError
from .verify import verify_canonical_load


def _require_ready(engine) -> None:
    if not database_is_reachable(engine):
        raise SystemExit(
            "Cannot reach the database. Start it with:\n"
            "    cd backend && docker compose up -d\n"
            "or set AVENTUM_DATABASE_URL to point at your instance."
        )
    if not table_exists(engine, "transactions"):
        raise SystemExit(
            "Schema is not migrated. Run:\n"
            "    cd backend && .venv/Scripts/python -m alembic upgrade head"
        )


def cmd_ingest(args: argparse.Namespace) -> int:
    config = load_config(source_path=args.source)
    engine = build_engine(config.database_url)
    _require_ready(engine)

    print(f"Source : {config.source_display_path}")
    print(f"Target : {config.database_url.rsplit('@', 1)[-1]}")
    print("-" * 72)

    try:
        result = run_ingestion(engine, config, force=args.force)
    except SourceIntegrityError as exc:
        print(f"SOURCE INTEGRITY FAILURE: {exc}", file=sys.stderr)
        return 2
    except SchemaDriftError as exc:
        print(f"SCHEMA DRIFT FAILURE (canonical table unchanged):\n{exc}", file=sys.stderr)
        return 3
    except UnknownDatasetError as exc:
        print(
            f"UNKNOWN DATASET -- ingestion refused (canonical data unchanged):\n{exc}",
            file=sys.stderr,
        )
        return 5
    except IngestionError as exc:
        print(f"INGESTION FAILURE: {exc}", file=sys.stderr)
        return 4

    print(f"Run id          : {result.ingestion_run_id}")
    print(f"Status          : {result.status}")
    if result.identity:
        print(f"Dataset identity: {result.identity.dataset_name}  (resolved from content hash)")
    if result.source:
        print(f"Source SHA-256  : {result.source.sha256}")
        print(f"Source size     : {result.source.size_bytes:,} bytes")
    if result.schema_drift:
        print(f"Schema drift    : {result.schema_drift.summary()}")
    print(f"Rows read       : {result.rows_read:,}")
    print(f"Rows valid      : {result.rows_valid:,}")
    print(f"Rows rejected   : {result.rows_rejected:,}")
    print(f"Rows inserted   : {result.rows_inserted:,}")
    print(f"Duration        : {result.duration_seconds:.2f}s")
    print(f"Fingerprint     : {result.canonical_fingerprint}")
    if result.reject_categories:
        print(f"Reject reasons  : {result.reject_categories}")
    if result.verification:
        print(f"Verification    : {result.verification.summary()}")
    if result.message:
        print(f"Note            : {result.message}")

    return 0 if result.succeeded else 1


def cmd_register(args: argparse.Namespace) -> int:
    """
    Register a dataset identity. Does NOT load or replace any canonical data.

    Binds dataset_name <-> source SHA-256 <-> schema_version so a later `ingest` of the
    same bytes resolves to a trusted provenance value.
    """
    config = load_config(source_path=args.source)
    engine = build_engine(config.database_url)
    _require_ready(engine)

    try:
        source = fingerprint_source(
            config.source_path, display_path=config.source_display_path
        )
    except SourceIntegrityError as exc:
        print(f"SOURCE INTEGRITY FAILURE: {exc}", file=sys.stderr)
        return 2

    try:
        with engine.begin() as connection:
            identity = register_dataset(
                connection,
                source=source,
                dataset_name=args.name,
                schema_version=args.schema_version,
                registered_by=args.registered_by,
                notes=args.notes,
            )
    except DatasetRegistrationError as exc:
        print(f"REGISTRATION REFUSED: {exc}", file=sys.stderr)
        return 6

    print(f"Registered dataset : {identity.dataset_name}")
    print(f"Source SHA-256     : {identity.source_sha256}")
    print(f"Schema version     : {identity.schema_version}")
    print(f"Source filename    : {identity.source_filename}  (metadata only, not identity)")
    print(f"Source size        : {identity.source_size_bytes:,} bytes")
    print()
    print("Registration records identity only. No canonical data was loaded or replaced.")
    print("To load it, run:  python -m aventum_ingest.cli ingest --source <path>")
    return 0


def cmd_datasets(args: argparse.Namespace) -> int:
    config = load_config()
    engine = build_engine(config.database_url)
    _require_ready(engine)

    with engine.connect() as connection:
        identities = list_datasets(connection)

    if not identities:
        print("No datasets registered.")
        return 0

    print(f"{'dataset_name':<28} {'schema':<8} sha256")
    for identity in identities:
        print(
            f"{identity.dataset_name:<28} {identity.schema_version:<8} "
            f"{identity.source_sha256}"
        )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    config = load_config()
    engine = build_engine(config.database_url)
    _require_ready(engine)

    report = verify_canonical_load(engine)
    print(report.summary())
    print(f"Canonical fingerprint: {report.canonical_fingerprint}")
    for check in report.checks:
        mark = "PASS" if check.passed else "FAIL"
        print(f"  [{mark}] {check.name}")
    return 0 if report.passed else 1


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config()
    engine = build_engine(config.database_url)
    _require_ready(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT ingestion_run_id, status, rows_read, rows_valid, rows_rejected,
                       rows_inserted, duration_seconds, started_at, source_sha256
                FROM ingestion_runs ORDER BY ingestion_run_id DESC LIMIT 20
                """
            )
        ).mappings().all()

    if not rows:
        print("No ingestion runs recorded.")
        return 0

    print(f"{'id':>4}  {'status':<20} {'read':>8} {'valid':>8} {'rej':>5} {'ins':>8}  sha256")
    for row in rows:
        print(
            f"{row['ingestion_run_id']:>4}  {row['status']:<20} {row['rows_read']:>8,} "
            f"{row['rows_valid']:>8,} {row['rows_rejected']:>5,} {row['rows_inserted']:>8,}  "
            f"{row['source_sha256'][:16]}..."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aventum_ingest",
        description="Aventum canonical transaction ingestion (Day 2A).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Run the canonical ingestion pipeline.")
    p_ingest.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest even if this exact source has already been ingested successfully.",
    )
    p_ingest.add_argument("--source", default=None, help="Override the source file path.")
    p_ingest.set_defaults(func=cmd_ingest)

    p_register = sub.add_parser(
        "register",
        help="Register a dataset identity (SHA-256 -> name). Does not load any data.",
    )
    p_register.add_argument("--source", required=True, help="Path to the dataset file.")
    p_register.add_argument("--name", required=True, help="Dataset name to bind to this file.")
    p_register.add_argument(
        "--schema-version",
        default=SCHEMA_VERSION,
        help=f"Ingestion-contract schema version to bind (default: {SCHEMA_VERSION}).",
    )
    p_register.add_argument("--registered-by", default=None, help="Who registered it (audit).")
    p_register.add_argument("--notes", default=None, help="Free-text note (audit).")
    p_register.set_defaults(func=cmd_register)

    p_datasets = sub.add_parser("datasets", help="List registered dataset identities.")
    p_datasets.set_defaults(func=cmd_datasets)

    p_verify = sub.add_parser("verify", help="Re-run post-load verification.")
    p_verify.set_defaults(func=cmd_verify)

    p_status = sub.add_parser("status", help="Show recent ingestion runs.")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
