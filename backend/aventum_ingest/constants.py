"""
Frozen vocabularies and expectations, transcribed from the Day 1 audit documents.

Every constant here traces to a specific docs/ statement. These are deliberately
hard-coded rather than inferred from the data at runtime: inferring the "expected"
universe from the file being validated would make validation vacuous (any drifted
file would define its own expectations and always pass).

Sources:
  - docs/AVENTUM_CANONICAL_SCHEMA.md   (field definitions, transformations)
  - docs/DATA_DICTIONARY.md            (allowed values per field)
  - docs/DATASET_INVENTORY.md          (source column list, observed ranges)
"""

from __future__ import annotations

from decimal import Decimal

# --------------------------------------------------------------------------
# Schema version of THIS ingestion contract.
# Bump when the source->canonical mapping changes in a way that would produce
# different canonical records from the same raw file.
# --------------------------------------------------------------------------
SCHEMA_VERSION = "1.0.0"

# Name of the canonical transaction dataset.
#
# IMPORTANT (P1-1): this is NOT the provenance value to write during ingestion. It was,
# and that was the defect -- every file routed through `--source` inherited this name
# regardless of its content. `transactions.source_dataset` and
# `ingestion_runs.source_dataset` are now populated from a dataset identity resolved
# against the file's SHA-256 (aventum_ingest/dataset_registry.py).
#
# Its only remaining uses are:
#   - the dataset that a standalone `cli verify` checks provenance against by default,
#   - documentation/reference.
# Never assign it to a record without first resolving identity from content.
CANONICAL_DATASET_NAME = "upi_transactions_2024"

# Relative path of the primary transaction source, from the repo root (aventum/).
SOURCE_RELATIVE_PATH = "data/raw/UPI Transactions 2024 Dataset/upi_transactions_2024.csv"

# SHA-256 of the exact file the Day 1 audit profiled. This pins which bytes the
# distribution expectations below describe: they are asserted only when the ingested
# file hashes to this value, and skipped (not silently "passed") for any other source.
AUDITED_SOURCE_SHA256 = "8e46a45fd12c3e9e75a7cf1ac73604bdd9b2bd72859e3374d0153256ac4c89b6"

# --------------------------------------------------------------------------
# Expected SOURCE schema (docs/DATASET_INVENTORY.md §2.3)
# Note the irregular source header names: two use spaces, not underscores.
# Order matters only for drift reporting, not for parsing.
# --------------------------------------------------------------------------
EXPECTED_SOURCE_COLUMNS: tuple[str, ...] = (
    "transaction id",
    "timestamp",
    "transaction type",
    "merchant_category",
    "amount (INR)",
    "transaction_status",
    "sender_age_group",
    "receiver_age_group",
    "sender_state",
    "sender_bank",
    "receiver_bank",
    "device_type",
    "network_type",
    "fraud_flag",
    "hour_of_day",
    "day_of_week",
    "is_weekend",
)

# Source columns the canonical mapping actually consumes. A missing column here is
# fatal; a missing column that is merely "expected but unused" is a warning.
REQUIRED_SOURCE_COLUMNS: frozenset[str] = frozenset({
    "transaction id",
    "timestamp",
    "transaction type",
    "merchant_category",
    "amount (INR)",
    "transaction_status",
    "sender_state",
    "sender_bank",
    "receiver_bank",
    "device_type",
    "network_type",
    "fraud_flag",
})

# Expected-but-deliberately-unmapped source columns.
# hour_of_day / day_of_week / is_weekend are exact deterministic derivatives of
# `timestamp` (docs/DATA_LEAKAGE_ANALYSIS.md §1) and carry zero independent
# information, so they are recomputed at query time rather than persisted.
# sender_age_group / receiver_age_group have no canonical field defined in
# docs/AVENTUM_CANONICAL_SCHEMA.md.
UNMAPPED_SOURCE_COLUMNS: frozenset[str] = frozenset({
    "sender_age_group",
    "receiver_age_group",
    "hour_of_day",
    "day_of_week",
    "is_weekend",
})

# --------------------------------------------------------------------------
# Canonical value vocabularies (docs/DATA_DICTIONARY.md)
# --------------------------------------------------------------------------
VALID_STATUSES: frozenset[str] = frozenset({"SUCCESS", "FAILED"})

VALID_PAYMENT_METHODS: frozenset[str] = frozenset({
    "P2P", "P2M", "Bill Payment", "Recharge",
})

# The payment method for which merchant_category must be NULLed
# (docs/AVENTUM_CANONICAL_SCHEMA.md "Payment Context" constraint).
P2P_PAYMENT_METHOD = "P2P"

VALID_MERCHANT_CATEGORIES: frozenset[str] = frozenset({
    "Grocery", "Food", "Shopping", "Fuel", "Other",
    "Utilities", "Transport", "Entertainment", "Healthcare", "Education",
})

VALID_DEVICES: frozenset[str] = frozenset({"Android", "iOS", "Web"})

VALID_NETWORKS: frozenset[str] = frozenset({"4G", "5G", "WiFi", "3G"})

# 10 of India's 28+ states/UTs — the fixed subset this dataset covers.
VALID_REGIONS: frozenset[str] = frozenset({
    "Maharashtra", "Uttar Pradesh", "Karnataka", "Tamil Nadu", "Delhi",
    "Telangana", "Gujarat", "Andhra Pradesh", "Rajasthan", "West Bengal",
})

# --------------------------------------------------------------------------
# Bank universe and legal-name alias table
# (docs/DATA_DICTIONARY.md `issuer_bank_full_name`, docs/DATASET_JOIN_ANALYSIS.md §2)
#
# 6 aliases were confirmed by automatic substring match against NPCI legal names;
# SBI and PNB are acronyms with no substring relationship and were added manually.
# Any bank without a CONFIRMED mapping must stay NULL -- never guessed.
# --------------------------------------------------------------------------
BANK_LEGAL_NAMES: dict[str, str] = {
    "SBI": "State Bank Of India",          # manual (acronym, no substring match)
    "HDFC": "HDFC Bank Ltd",               # auto (substring)
    "ICICI": "ICICI Bank",                 # auto (substring)
    "IndusInd": "IndusInd Bank",           # auto (substring)
    "Axis": "Axis Bank Ltd",               # auto (substring)
    "PNB": "Punjab National Bank",         # manual (acronym, no substring match)
    "Yes Bank": "Yes Bank Ltd",            # auto (exact, normalized)
    "Kotak": "Kotak Mahindra Bank",        # auto (substring)
}

VALID_BANKS: frozenset[str] = frozenset(BANK_LEGAL_NAMES)

# --------------------------------------------------------------------------
# Timestamp handling (docs/AVENTUM_CANONICAL_SCHEMA.md Transaction.timestamp)
#
# "Parse to timestamptz; assume IST (unstated in source)". The source carries NO
# timezone designator, so the documented assumption is applied and recorded in
# ingestion metadata. This is an ASSUMPTION, not an independently verified fact.
# Fixed +05:30 offset: India has no DST, so this is unambiguous for all 2024 dates.
# --------------------------------------------------------------------------
SOURCE_TIMEZONE = "Asia/Kolkata"
SOURCE_TIMEZONE_UTC_OFFSET = "+05:30"
SOURCE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
TIMESTAMP_ASSUMPTION_NOTE = (
    "Source timestamps carry no timezone designator. docs/AVENTUM_CANONICAL_SCHEMA.md "
    "documents the assumption that they are IST (Asia/Kolkata, UTC+05:30). Applied as "
    "an assumption and recorded here; NOT independently verified."
)

# Audited source timestamp range (docs/DATA_DICTIONARY.md `timestamp`), in IST.
# Used as an inclusive validation bound; a row outside this window means either the
# source changed or the audit is stale -- both warrant rejection, not silent acceptance.
EXPECTED_TIMESTAMP_MIN_IST = "2024-01-01 00:05:10"
EXPECTED_TIMESTAMP_MAX_IST = "2024-12-30 23:55:40"

# --------------------------------------------------------------------------
# Amount bounds (docs/DATA_DICTIONARY.md `amount`: 10-42,099, integer in source)
# CHECK (amount > 0) is the hard DB invariant; these bounds are the softer
# "matches the audited source" validation.
# --------------------------------------------------------------------------
EXPECTED_AMOUNT_MIN = Decimal("10")
EXPECTED_AMOUNT_MAX = Decimal("42099")

# --------------------------------------------------------------------------
# Day 1 audited invariants, asserted by post-load verification (Day 2A §13).
# docs/DAY1_REPORT.md / docs/DATA_QUALITY_REPORT.md
# --------------------------------------------------------------------------
EXPECTED_ROW_COUNT = 250_000
EXPECTED_STATUS_DISTRIBUTION: dict[str, int] = {
    "SUCCESS": 237_624,
    "FAILED": 12_376,
}
EXPECTED_PAYMENT_METHOD_DISTRIBUTION: dict[str, int] = {
    "P2P": 112_445,
    "P2M": 87_660,
    "Bill Payment": 37_368,
    "Recharge": 12_527,
}
EXPECTED_DEVICE_DISTRIBUTION: dict[str, int] = {
    "Android": 187_777,
    "iOS": 49_613,
    "Web": 12_610,
}
EXPECTED_NETWORK_DISTRIBUTION: dict[str, int] = {
    "4G": 149_813,
    "5G": 62_582,
    "WiFi": 25_134,
    "3G": 12_471,
}
EXPECTED_FRAUD_FLAG_TRUE_COUNT = 480
