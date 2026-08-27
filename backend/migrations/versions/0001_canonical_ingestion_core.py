"""Day 2A canonical ingestion core: banks, ingestion_runs, transactions, staging, rejects.

Creates ONLY the structures the canonical transaction ingestion pipeline needs.
Later-phase tables (gateways, gateway_metrics, routing_policies, incidents,
incident_evidence, simulations, simulation_results, recommendations, actions,
verification_results, audit_events) are deliberately out of scope.

Revision ID: 0001
Revises:
Create Date: Day 2A
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


# Frozen at migration-authoring time. These mirror aventum_ingest.constants but are
# duplicated intentionally: a migration must describe the schema as it was created,
# and must not change retroactively if application constants are later edited.
_STATUSES = ("FAILED", "SUCCESS")
_PAYMENT_METHODS = ("Bill Payment", "P2M", "P2P", "Recharge")
_DEVICES = ("Android", "Web", "iOS")
_NETWORKS = ("3G", "4G", "5G", "WiFi")
_REGIONS = (
    "Andhra Pradesh", "Delhi", "Gujarat", "Karnataka", "Maharashtra",
    "Rajasthan", "Tamil Nadu", "Telangana", "Uttar Pradesh", "West Bengal",
)

# bank_code -> (legal_name, npci_reference_available)
# Only CONFIRMED aliases (docs/DATASET_JOIN_ANALYSIS.md §2) are seeded; nothing guessed.
_BANK_SEED = (
    ("SBI", "State Bank Of India", True),
    ("HDFC", "HDFC Bank Ltd", True),
    ("ICICI", "ICICI Bank", True),
    ("IndusInd", "IndusInd Bank", True),
    ("Axis", "Axis Bank Ltd", True),
    ("PNB", "Punjab National Bank", True),
    ("Yes Bank", "Yes Bank Ltd", True),
    ("Kotak", "Kotak Mahindra Bank", True),
)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def upgrade() -> None:
    # ---------------------------------------------------------------- banks
    op.create_table(
        "banks",
        sa.Column("bank_code", sa.Text(), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=True),
        sa.Column(
            "npci_reference_available",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.PrimaryKeyConstraint("bank_code", name="pk_banks"),
    )

    banks_table = sa.table(
        "banks",
        sa.column("bank_code", sa.Text),
        sa.column("legal_name", sa.Text),
        sa.column("npci_reference_available", sa.Boolean),
    )
    op.bulk_insert(
        banks_table,
        [
            {"bank_code": code, "legal_name": legal, "npci_reference_available": available}
            for code, legal, available in _BANK_SEED
        ],
    )

    # ------------------------------------------------------- ingestion_runs
    op.create_table(
        "ingestion_runs",
        sa.Column("ingestion_run_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("source_filename", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_dataset", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("code_version", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rows_read", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_valid", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_rejected", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_inserted", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("schema_drift_report", postgresql.JSONB(), nullable=True),
        sa.Column("timestamp_assumption", sa.Text(), nullable=True),
        sa.Column("canonical_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("verification_report", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("ingestion_run_id", name="pk_ingestion_runs"),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED_IDEMPOTENT')",
            name="ck_ingestion_runs_status",
        ),
        sa.CheckConstraint("rows_read >= 0", name="ck_ingestion_runs_rows_read"),
        sa.CheckConstraint("rows_valid >= 0", name="ck_ingestion_runs_rows_valid"),
        sa.CheckConstraint("rows_rejected >= 0", name="ck_ingestion_runs_rows_rejected"),
        sa.CheckConstraint("rows_inserted >= 0", name="ck_ingestion_runs_rows_inserted"),
        sa.CheckConstraint("length(source_sha256) = 64", name="ck_ingestion_runs_sha256_length"),
        sa.CheckConstraint(
            "status = 'RUNNING' OR rows_valid + rows_rejected = rows_read",
            name="ck_ingestion_runs_row_accounting",
        ),
    )
    op.create_index("ix_ingestion_runs_sha256", "ingestion_runs", ["source_sha256"])
    op.create_index("ix_ingestion_runs_status", "ingestion_runs", ["status"])

    # --------------------------------------------------------- transactions
    op.create_table(
        "transactions",
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payment_method", sa.Text(), nullable=False),
        # Generated alias of payment_method. See the DOCUMENTED SCHEMA DEVIATION note in
        # aventum_ingest/models.py: the canonical schema defines transaction_type as
        # "same value as payment_method", so it is generated rather than duplicated,
        # making divergence structurally impossible.
        sa.Column(
            "transaction_type",
            sa.Text(),
            sa.Computed("payment_method", persisted=True),
            nullable=False,
        ),
        sa.Column("merchant_category", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("device", sa.Text(), nullable=False),
        sa.Column("network", sa.Text(), nullable=False),
        sa.Column("sender_bank", sa.Text(), nullable=False),
        sa.Column("receiver_bank", sa.Text(), nullable=False),
        # Generated alias of sender_bank, same rationale as transaction_type.
        sa.Column(
            "issuer_bank",
            sa.Text(),
            sa.Computed("sender_bank", persisted=True),
            nullable=False,
        ),
        sa.Column("fraud_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "source_dataset",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'upi_transactions_2024'"),
        ),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("transaction_id", name="pk_transactions"),
        sa.ForeignKeyConstraint(
            ["sender_bank"], ["banks.bank_code"], name="fk_transactions_sender_bank"
        ),
        sa.ForeignKeyConstraint(
            ["receiver_bank"], ["banks.bank_code"], name="fk_transactions_receiver_bank"
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.ingestion_run_id"],
            name="fk_transactions_ingestion_run",
        ),
        sa.CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        sa.CheckConstraint(f"status IN ({_in_list(_STATUSES)})", name="ck_transactions_status"),
        sa.CheckConstraint(
            f"payment_method IN ({_in_list(_PAYMENT_METHODS)})",
            name="ck_transactions_payment_method",
        ),
        sa.CheckConstraint(f"device IN ({_in_list(_DEVICES)})", name="ck_transactions_device"),
        sa.CheckConstraint(f"network IN ({_in_list(_NETWORKS)})", name="ck_transactions_network"),
        sa.CheckConstraint(f"region IN ({_in_list(_REGIONS)})", name="ck_transactions_region"),
        sa.CheckConstraint(
            "(payment_method = 'P2P' AND merchant_category IS NULL) "
            "OR (payment_method <> 'P2P' AND merchant_category IS NOT NULL)",
            name="ck_transactions_p2p_merchant_category",
        ),
        sa.CheckConstraint("length(transaction_id) > 0", name="ck_transactions_id_nonempty"),
        sa.CheckConstraint("length(source_dataset) > 0", name="ck_transactions_source_nonempty"),
    )
    op.create_index("ix_transactions_timestamp", "transactions", ["timestamp"])
    op.create_index(
        "ix_transactions_sender_bank_timestamp", "transactions", ["sender_bank", "timestamp"]
    )
    op.create_index("ix_transactions_status_timestamp", "transactions", ["status", "timestamp"])
    op.create_index("ix_transactions_region_timestamp", "transactions", ["region", "timestamp"])
    op.create_index("ix_transactions_ingestion_run", "transactions", ["ingestion_run_id"])

    # ------------------------------------------------- transactions_staging
    # Same invariants as `transactions` so a validation bug fails here, while the
    # authoritative table is still untouched.
    op.create_table(
        "transactions_staging",
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payment_method", sa.Text(), nullable=False),
        sa.Column("merchant_category", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("device", sa.Text(), nullable=False),
        sa.Column("network", sa.Text(), nullable=False),
        sa.Column("sender_bank", sa.Text(), nullable=False),
        sa.Column("receiver_bank", sa.Text(), nullable=False),
        sa.Column("fraud_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source_dataset", sa.Text(), nullable=False),
        sa.Column("source_row_index", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("transaction_id", name="pk_transactions_staging"),
        sa.CheckConstraint("amount > 0", name="ck_staging_amount_positive"),
        sa.CheckConstraint(f"status IN ({_in_list(_STATUSES)})", name="ck_staging_status"),
        sa.CheckConstraint(
            f"payment_method IN ({_in_list(_PAYMENT_METHODS)})", name="ck_staging_payment_method"
        ),
        sa.CheckConstraint(f"device IN ({_in_list(_DEVICES)})", name="ck_staging_device"),
        sa.CheckConstraint(f"network IN ({_in_list(_NETWORKS)})", name="ck_staging_network"),
        sa.CheckConstraint(f"region IN ({_in_list(_REGIONS)})", name="ck_staging_region"),
        sa.CheckConstraint(
            "(payment_method = 'P2P' AND merchant_category IS NULL) "
            "OR (payment_method <> 'P2P' AND merchant_category IS NOT NULL)",
            name="ck_staging_p2p_merchant_category",
        ),
    )

    # ----------------------------------------------------- ingestion_rejects
    op.create_table(
        "ingestion_rejects",
        sa.Column("reject_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=False),
        sa.Column("source_row_index", sa.BigInteger(), nullable=False),
        sa.Column("transaction_id", sa.Text(), nullable=True),
        sa.Column("validation_error", sa.Text(), nullable=False),
        sa.Column("error_category", sa.Text(), nullable=False),
        sa.Column("offending_fields", postgresql.JSONB(), nullable=True),
        sa.Column("raw_record", postgresql.JSONB(), nullable=False),
        sa.Column(
            "rejected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("reject_id", name="pk_ingestion_rejects"),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.ingestion_run_id"],
            name="fk_rejects_ingestion_run",
        ),
    )
    op.create_index("ix_ingestion_rejects_run", "ingestion_rejects", ["ingestion_run_id"])
    op.create_index("ix_ingestion_rejects_category", "ingestion_rejects", ["error_category"])

    # --------------------------------------------- canonical consumer view
    # Exposes issuer_bank_full_name (canonical schema class: derived) by joining the
    # banks dimension, instead of denormalizing legal_name onto every transaction row.
    op.execute(
        """
        CREATE VIEW v_transactions_canonical AS
        SELECT
            t.transaction_id,
            t.timestamp,
            t.amount,
            t.status,
            t.payment_method,
            t.transaction_type,
            t.merchant_category,
            t.region,
            t.device,
            t.network,
            t.sender_bank,
            t.receiver_bank,
            t.issuer_bank,
            b.legal_name AS issuer_bank_full_name,
            t.fraud_flag,
            t.source_dataset,
            t.ingestion_run_id
        FROM transactions t
        LEFT JOIN banks b ON b.bank_code = t.issuer_bank
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_transactions_canonical")
    op.drop_index("ix_ingestion_rejects_category", table_name="ingestion_rejects")
    op.drop_index("ix_ingestion_rejects_run", table_name="ingestion_rejects")
    op.drop_table("ingestion_rejects")
    op.drop_table("transactions_staging")
    op.drop_index("ix_transactions_ingestion_run", table_name="transactions")
    op.drop_index("ix_transactions_region_timestamp", table_name="transactions")
    op.drop_index("ix_transactions_status_timestamp", table_name="transactions")
    op.drop_index("ix_transactions_sender_bank_timestamp", table_name="transactions")
    op.drop_index("ix_transactions_timestamp", table_name="transactions")
    op.drop_table("transactions")
    op.drop_index("ix_ingestion_runs_status", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_sha256", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    op.drop_table("banks")
