"""
SQLAlchemy models for the Day 2A canonical ingestion scope.

Only tables this pipeline needs are defined here. Later-phase infrastructure tables
(gateways, gateway_metrics, routing_policies, incidents, incident_evidence,
simulations, simulation_results, recommendations, actions, verification_results,
audit_events) are deliberately NOT created -- they belong to later tasks.

--------------------------------------------------------------------------------
DOCUMENTED SCHEMA DEVIATION (see docs/DAY2A_INGESTION_REPORT.md "Deviations")
--------------------------------------------------------------------------------
Contradiction found between source-of-truth documents:

  docs/AVENTUM_CANONICAL_SCHEMA.md and docs/DATA_DICTIONARY.md both define
  `transaction_type`, `issuer_bank`, and `issuer_bank_full_name` as canonical fields.
  docs/DATABASE_DESIGN.md's physical `transactions` column list omits all three.
  Day 2A §5 explicitly requires the mappings
  `transaction type -> transaction_type` and `sender_bank -> issuer_bank`.

Why it matters: implementing only DATABASE_DESIGN.md would silently drop two fields
the canonical schema promises to consumers; implementing them as ordinary duplicated
columns would create two mutable copies of the same fact that can drift apart.

Minimum justified change:
  1. `transaction_type` and `issuer_bank` are added to `transactions` as PostgreSQL
     GENERATED ALWAYS ... STORED columns over `payment_method` and `sender_bank`.
     This satisfies the canonical schema's explicit "same value as" / "copy of"
     wording, and makes divergence structurally impossible rather than merely
     validated -- which is what §8 (defense in depth) asks for.
  2. `issuer_bank_full_name` is NOT added as a stored column. `banks.legal_name`
     already holds exactly that value per DATABASE_DESIGN.md, and the canonical
     schema classes the field `derived`. It is exposed through the
     `v_transactions_canonical` view instead, keeping one source of truth.
--------------------------------------------------------------------------------
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .constants import (
    VALID_DEVICES,
    VALID_NETWORKS,
    VALID_PAYMENT_METHODS,
    VALID_REGIONS,
    VALID_STATUSES,
)


class Base(DeclarativeBase):
    pass


def _sql_in_list(values: frozenset[str] | set[str]) -> str:
    """Render a deterministic, SQL-quoted IN-list. Sorted so DDL is reproducible."""
    escaped = (v.replace("'", "''") for v in sorted(values))
    return ", ".join(f"'{v}'" for v in escaped)


class DatasetRegistry(Base):
    """
    Trusted binding between a source file's CONTENT HASH and a dataset name (P1-1).

    The primary key is the SHA-256, not the name: identity is established by content,
    never by filename. `dataset_name` is UNIQUE so a known dataset name can never be
    rebound to different bytes. See aventum_ingest/dataset_registry.py.
    """

    __tablename__ = "dataset_registry"

    source_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    # Metadata only -- never consulted during identity resolution.
    source_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    registered_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    registered_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("length(source_sha256) = 64", name="ck_dataset_registry_sha256_length"),
        CheckConstraint("length(dataset_name) > 0", name="ck_dataset_registry_name_nonempty"),
        CheckConstraint(
            "length(schema_version) > 0", name="ck_dataset_registry_schema_version_nonempty"
        ),
    )


class Bank(Base):
    """
    Dimension table for the bank universe (docs/DATABASE_DESIGN.md `banks`).

    Seeded with the 8 banks observed in upi_transactions_2024. `legal_name` is
    populated only where a CONFIRMED NPCI alias exists -- never guessed.
    """

    __tablename__ = "banks"

    bank_code: Mapped[str] = mapped_column(Text, primary_key=True)
    legal_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    npci_reference_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )


class Transaction(Base):
    """
    Canonical payment-event fact table.

    Every constraint below is defense in depth: the ETL validates the same invariants
    before promotion, but the database rejects violations independently so an upstream
    validation bug cannot produce an invalid canonical state (Day 2A §8).
    """

    __tablename__ = "transactions"

    # --- Transaction (canonical schema "Transaction" group) ---
    transaction_id: Mapped[str] = mapped_column(Text, primary_key=True)
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[object] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Payment context ---
    payment_method: Mapped[str] = mapped_column(Text, nullable=False)
    # Generated alias of payment_method -- see the deviation note in this module's docstring.
    transaction_type: Mapped[str] = mapped_column(
        Text, Computed("payment_method", persisted=True), nullable=False
    )
    merchant_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    device: Mapped[str] = mapped_column(Text, nullable=False)
    network: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Banking / issuer ---
    sender_bank: Mapped[str] = mapped_column(
        Text, ForeignKey("banks.bank_code", name="fk_transactions_sender_bank"), nullable=False
    )
    receiver_bank: Mapped[str] = mapped_column(
        Text, ForeignKey("banks.bank_code", name="fk_transactions_receiver_bank"), nullable=False
    )
    # Generated alias of sender_bank -- see the deviation note in this module's docstring.
    issuer_bank: Mapped[str] = mapped_column(
        Text, Computed("sender_bank", persisted=True), nullable=False
    )

    # --- Retained source field (post-hoc label; never a live-scoring feature) ---
    fraud_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # --- Provenance ---
    # No server default: provenance must always be supplied explicitly from a resolved
    # dataset identity (P1-1). A default here would let an INSERT that omits the column
    # silently acquire the canonical dataset's name.
    source_dataset: Mapped[str] = mapped_column(Text, nullable=False)
    ingestion_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.ingestion_run_id", name="fk_transactions_ingestion_run"),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        CheckConstraint(
            f"status IN ({_sql_in_list(VALID_STATUSES)})", name="ck_transactions_status"
        ),
        CheckConstraint(
            f"payment_method IN ({_sql_in_list(VALID_PAYMENT_METHODS)})",
            name="ck_transactions_payment_method",
        ),
        CheckConstraint(
            f"device IN ({_sql_in_list(VALID_DEVICES)})", name="ck_transactions_device"
        ),
        CheckConstraint(
            f"network IN ({_sql_in_list(VALID_NETWORKS)})", name="ck_transactions_network"
        ),
        CheckConstraint(
            f"region IN ({_sql_in_list(VALID_REGIONS)})", name="ck_transactions_region"
        ),
        # The P2P cleaning rule, enforced in BOTH directions:
        # P2P must have no merchant category; every non-P2P row must retain one.
        CheckConstraint(
            "(payment_method = 'P2P' AND merchant_category IS NULL) "
            "OR (payment_method <> 'P2P' AND merchant_category IS NOT NULL)",
            name="ck_transactions_p2p_merchant_category",
        ),
        CheckConstraint("length(transaction_id) > 0", name="ck_transactions_id_nonempty"),
        CheckConstraint("length(source_dataset) > 0", name="ck_transactions_source_nonempty"),
        Index("ix_transactions_timestamp", "timestamp"),
        Index("ix_transactions_sender_bank_timestamp", "sender_bank", "timestamp"),
        Index("ix_transactions_status_timestamp", "status", "timestamp"),
        Index("ix_transactions_region_timestamp", "region", "timestamp"),
        Index("ix_transactions_ingestion_run", "ingestion_run_id"),
    )


class TransactionStaging(Base):
    """
    Staging table for the atomic load (Day 2A §10).

    Carries the SAME constraints as `transactions` (minus the FK to ingestion_runs,
    which is applied at promotion) so that a validation bug fails here -- while the
    authoritative table is still untouched -- rather than after promotion.

    Truncated at the start of every run; never read by downstream consumers.
    """

    __tablename__ = "transactions_staging"

    transaction_id: Mapped[str] = mapped_column(Text, primary_key=True)
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[object] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    payment_method: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    device: Mapped[str] = mapped_column(Text, nullable=False)
    network: Mapped[str] = mapped_column(Text, nullable=False)
    sender_bank: Mapped[str] = mapped_column(Text, nullable=False)
    receiver_bank: Mapped[str] = mapped_column(Text, nullable=False)
    fraud_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    source_dataset: Mapped[str] = mapped_column(Text, nullable=False)
    source_row_index: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_staging_amount_positive"),
        CheckConstraint(f"status IN ({_sql_in_list(VALID_STATUSES)})", name="ck_staging_status"),
        CheckConstraint(
            f"payment_method IN ({_sql_in_list(VALID_PAYMENT_METHODS)})",
            name="ck_staging_payment_method",
        ),
        CheckConstraint(f"device IN ({_sql_in_list(VALID_DEVICES)})", name="ck_staging_device"),
        CheckConstraint(f"network IN ({_sql_in_list(VALID_NETWORKS)})", name="ck_staging_network"),
        CheckConstraint(f"region IN ({_sql_in_list(VALID_REGIONS)})", name="ck_staging_region"),
        CheckConstraint(
            "(payment_method = 'P2P' AND merchant_category IS NULL) "
            "OR (payment_method <> 'P2P' AND merchant_category IS NOT NULL)",
            name="ck_staging_p2p_merchant_category",
        ),
    )


class IngestionRun(Base):
    """
    One row per ingestion attempt -- the audit record proving which source file
    produced which canonical rows (Day 2A §2).

    Rows are never deleted or rewritten after completion; a retry creates a new run.
    """

    __tablename__ = "ingestion_runs"

    ingestion_run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # --- Source identity ---
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_filename: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_dataset: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Contract / code identity (determinism inputs) ---
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    code_version: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Lifecycle ---
    started_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[object | None] = mapped_column(Numeric(12, 3), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Counters ---
    rows_read: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rows_valid: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rows_inserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # --- Evidence ---
    schema_drift_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    timestamp_assumption: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED_IDEMPOTENT')",
            name="ck_ingestion_runs_status",
        ),
        CheckConstraint("rows_read >= 0", name="ck_ingestion_runs_rows_read"),
        CheckConstraint("rows_valid >= 0", name="ck_ingestion_runs_rows_valid"),
        CheckConstraint("rows_rejected >= 0", name="ck_ingestion_runs_rows_rejected"),
        CheckConstraint("rows_inserted >= 0", name="ck_ingestion_runs_rows_inserted"),
        CheckConstraint("length(source_sha256) = 64", name="ck_ingestion_runs_sha256_length"),
        # A finished run must account for every row it read.
        CheckConstraint(
            "status = 'RUNNING' OR rows_valid + rows_rejected = rows_read",
            name="ck_ingestion_runs_row_accounting",
        ),
        Index("ix_ingestion_runs_sha256", "source_sha256"),
        Index("ix_ingestion_runs_status", "status"),
    )


class IngestionReject(Base):
    """
    Quarantine for records that failed validation (Day 2A §9).

    Records are never silently discarded: every rejection keeps the source row index,
    the failing rule, the responsible field(s), and the raw payload so an engineer can
    reconstruct exactly why the row did not become canonical.
    """

    __tablename__ = "ingestion_rejects"

    reject_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ingestion_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.ingestion_run_id", name="fk_rejects_ingestion_run"),
        nullable=False,
    )
    source_row_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transaction_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_error: Mapped[str] = mapped_column(Text, nullable=False)
    error_category: Mapped[str] = mapped_column(Text, nullable=False)
    offending_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_record: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rejected_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_ingestion_rejects_run", "ingestion_run_id"),
        Index("ix_ingestion_rejects_category", "error_category"),
    )
