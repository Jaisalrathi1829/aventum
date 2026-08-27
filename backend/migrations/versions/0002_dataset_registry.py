"""Trusted dataset registry (P1-1 provenance fix).

WHY A MIGRATION IS REQUIRED
---------------------------
The Day 2A review's P1-1 fix requires dataset identity to be resolved from a trusted
SHA-256 -> dataset_name binding rather than from a hard-coded constant. That binding
must persist, because `cli register` creates new bindings at runtime that later
`cli ingest` invocations must be able to resolve. A code constant cannot hold runtime
registrations, and a config file would be a parallel state store for something the
database already models well (and would not be auditable alongside `ingestion_runs`).

This is the minimum justified change:
  1. CREATE the `dataset_registry` table.
  2. SEED the one already-verified identity (upi_transactions_2024).
  3. DROP the `transactions.source_dataset` server default, which was itself a
     hard-coded provenance value: any INSERT omitting the column silently acquired the
     canonical dataset's name. The ETL always supplies the value explicitly, so nothing
     depends on the default.

No change is made to `transaction_id`, the primary key, `ingestion_run_id`, observed
field definitions, generated columns, or anything else the Day 2B interface depends on.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


# The one dataset whose identity is already verified: Day 1 audited this exact file and
# the Day 2A review re-confirmed the hash independently. Seeding it here is not
# "hard-coding provenance" -- the name is only ever assigned to a file that hashes to
# this value, which is precisely the content-over-filename trust boundary.
_CANONICAL_DATASET_NAME = "upi_transactions_2024"
_CANONICAL_SHA256 = "8e46a45fd12c3e9e75a7cf1ac73604bdd9b2bd72859e3374d0153256ac4c89b6"
_CANONICAL_SCHEMA_VERSION = "1.0.0"
_CANONICAL_FILENAME = "upi_transactions_2024.csv"
_CANONICAL_SIZE_BYTES = 29811789


def upgrade() -> None:
    op.create_table(
        "dataset_registry",
        # Content hash is the identity key -- the primary key, not the name.
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("dataset_name", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        # Filename/size are METADATA ONLY. They are never consulted during identity
        # resolution; recording them just aids human auditing.
        sa.Column("source_filename", sa.Text(), nullable=True),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("registered_by", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("source_sha256", name="pk_dataset_registry"),
        # One name maps to exactly one content hash, and vice versa. This is what stops
        # a known dataset name from being rebound to different bytes.
        sa.UniqueConstraint("dataset_name", name="uq_dataset_registry_name"),
        sa.CheckConstraint(
            "length(source_sha256) = 64", name="ck_dataset_registry_sha256_length"
        ),
        sa.CheckConstraint("length(dataset_name) > 0", name="ck_dataset_registry_name_nonempty"),
        sa.CheckConstraint(
            "length(schema_version) > 0", name="ck_dataset_registry_schema_version_nonempty"
        ),
    )

    registry = sa.table(
        "dataset_registry",
        sa.column("source_sha256", sa.String),
        sa.column("dataset_name", sa.Text),
        sa.column("schema_version", sa.Text),
        sa.column("source_filename", sa.Text),
        sa.column("source_size_bytes", sa.BigInteger),
        sa.column("registered_by", sa.Text),
        sa.column("notes", sa.Text),
    )
    op.bulk_insert(
        registry,
        [
            {
                "source_sha256": _CANONICAL_SHA256,
                "dataset_name": _CANONICAL_DATASET_NAME,
                "schema_version": _CANONICAL_SCHEMA_VERSION,
                "source_filename": _CANONICAL_FILENAME,
                "source_size_bytes": _CANONICAL_SIZE_BYTES,
                "registered_by": "migration:0002",
                "notes": (
                    "Primary transaction backbone. Hash verified by the Day 1 audit and "
                    "re-verified independently by the Day 2A architecture review."
                ),
            }
        ],
    )

    # Remove the forgeable default: an INSERT omitting source_dataset previously acquired
    # the canonical dataset's name for free. Provenance must always be supplied explicitly
    # by the ingestion pipeline from a resolved identity.
    op.alter_column("transactions", "source_dataset", server_default=None)


def downgrade() -> None:
    op.alter_column(
        "transactions",
        "source_dataset",
        server_default=sa.text("'upi_transactions_2024'"),
    )
    op.drop_table("dataset_registry")
