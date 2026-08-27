"""
Deterministic source -> canonical normalization (Day 2A §4, §5, §6).

Determinism contract: same raw file + same code version + same config => byte-identical
canonical records. Therefore this module uses
  - no randomness,
  - no wall-clock time as a transformation input,
  - no locale-dependent parsing (explicit timestamp format, explicit Decimal),
  - a fixed, explicit row ordering (source order, preserved as source_row_index).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from .constants import (
    P2P_PAYMENT_METHOD,
    SOURCE_TIMESTAMP_FORMAT,
)

# India Standard Time is a fixed UTC+05:30 with no daylight saving, so a fixed-offset
# tzinfo is exact for every timestamp in this dataset and avoids depending on the host
# machine's tz database (a hidden non-determinism source).
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# Canonical column order. Fixed so the staging COPY and the dataset fingerprint are
# reproducible regardless of dict iteration details.
CANONICAL_COLUMNS: tuple[str, ...] = (
    "transaction_id",
    "timestamp",
    "amount",
    "status",
    "payment_method",
    "merchant_category",
    "region",
    "device",
    "network",
    "sender_bank",
    "receiver_bank",
    "fraud_flag",
    "source_dataset",
    "source_row_index",
)


class NormalizationError(ValueError):
    """A single field could not be deterministically normalized."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


def _clean_text(value: Any) -> str | None:
    """Trim surrounding whitespace; treat empty/NA as absent. Casing is NOT altered."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def normalize_transaction_id(value: Any) -> str:
    text = _clean_text(value)
    if text is None:
        raise NormalizationError("transaction_id is missing or empty", "transaction_id")
    return text


def normalize_timestamp(value: Any) -> datetime:
    """
    Parse a naive source timestamp and attach the documented IST assumption.

    docs/AVENTUM_CANONICAL_SCHEMA.md: "Parse to timestamptz; assume IST (unstated in
    source)". The assumption is applied here and recorded in ingestion metadata; it is
    never represented as independently verified fact.

    An explicit format string is used so parsing cannot silently fall back to a
    locale- or heuristic-dependent interpretation (e.g. DD/MM vs MM/DD).
    """
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _clean_text(value)
        if text is None:
            raise NormalizationError("timestamp is missing or empty", "timestamp")
        try:
            parsed = datetime.strptime(text, SOURCE_TIMESTAMP_FORMAT)
        except ValueError as exc:
            raise NormalizationError(
                f"timestamp {text!r} does not match expected format "
                f"{SOURCE_TIMESTAMP_FORMAT!r}: {exc}",
                "timestamp",
            ) from exc

    if parsed.tzinfo is not None:
        # Source is documented as naive; a tz-aware value means the source changed.
        raise NormalizationError(
            f"timestamp {parsed!r} unexpectedly carries a timezone; source is documented "
            "as timezone-naive and the IST assumption would be ambiguous",
            "timestamp",
        )

    return parsed.replace(tzinfo=IST)


def normalize_amount(value: Any) -> Decimal:
    """Convert to Decimal at the canonical numeric(12,2) scale. No float arithmetic."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        raise NormalizationError("amount is missing", "amount")
    try:
        # str() first: constructing Decimal directly from a float would inherit binary
        # floating-point error into a monetary value.
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise NormalizationError(f"amount {value!r} is not numeric: {exc}", "amount") from exc

    if not amount.is_finite():
        raise NormalizationError(f"amount {value!r} is not finite", "amount")

    return amount.quantize(Decimal("0.01"))


def normalize_status(value: Any) -> str:
    """
    Canonicalize to the documented SUCCESS/FAILED vocabulary.

    Upper-casing is a canonicalization step, not a coercion: it maps the source's
    already-uppercase values onto the canonical enum without inventing membership.
    Vocabulary membership itself is enforced by validation, not here.
    """
    text = _clean_text(value)
    if text is None:
        raise NormalizationError("status is missing or empty", "status")
    return text.upper()


def normalize_payment_method(value: Any) -> str:
    text = _clean_text(value)
    if text is None:
        raise NormalizationError("payment_method is missing or empty", "payment_method")
    return text


def normalize_merchant_category(value: Any, payment_method: str) -> str | None:
    """
    Apply the documented P2P cleaning rule.

    docs/AVENTUM_CANONICAL_SCHEMA.md: merchant_category is "set to NULL at load time
    for rows where payment_method = 'P2P'", because no real merchant exists for a
    person-to-person transfer even though the raw source populates the column.

    For non-P2P rows the source value is preserved verbatim -- no merchant information
    is ever invented for a row that lacks it.
    """
    if payment_method == P2P_PAYMENT_METHOD:
        return None
    return _clean_text(value)


def normalize_simple_text(value: Any, field: str) -> str:
    text = _clean_text(value)
    if text is None:
        raise NormalizationError(f"{field} is missing or empty", field)
    return text


def normalize_fraud_flag(value: Any) -> bool:
    """Source encodes fraud_flag as integer 0/1; accept common boolean spellings."""
    if isinstance(value, bool):
        return value
    if value is None or (isinstance(value, float) and pd.isna(value)):
        raise NormalizationError("fraud_flag is missing", "fraud_flag")
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    raise NormalizationError(f"fraud_flag {value!r} is not a recognized boolean", "fraud_flag")


def normalize_row(
    raw: dict[str, Any], source_row_index: int, source_dataset: str
) -> dict[str, Any]:
    """
    Map one raw source row to the canonical record.

    `source_dataset` is a REQUIRED argument supplied by the caller from a resolved
    dataset identity (aventum_ingest/dataset_registry.py). It is deliberately not a
    module constant: hard-coding it was P1-1, which let any file be labelled
    `upi_transactions_2024` regardless of its actual content.

    Raises NormalizationError on the first field that cannot be deterministically
    converted; the caller quarantines the row rather than guessing a value.
    """
    if not source_dataset:
        raise NormalizationError(
            "source_dataset must be supplied from a resolved dataset identity",
            "source_dataset",
        )

    payment_method = normalize_payment_method(raw.get("transaction type"))

    return {
        "transaction_id": normalize_transaction_id(raw.get("transaction id")),
        "timestamp": normalize_timestamp(raw.get("timestamp")),
        "amount": normalize_amount(raw.get("amount (INR)")),
        "status": normalize_status(raw.get("transaction_status")),
        "payment_method": payment_method,
        "merchant_category": normalize_merchant_category(
            raw.get("merchant_category"), payment_method
        ),
        "region": normalize_simple_text(raw.get("sender_state"), "region"),
        "device": normalize_simple_text(raw.get("device_type"), "device"),
        "network": normalize_simple_text(raw.get("network_type"), "network"),
        "sender_bank": normalize_simple_text(raw.get("sender_bank"), "sender_bank"),
        "receiver_bank": normalize_simple_text(raw.get("receiver_bank"), "receiver_bank"),
        "fraud_flag": normalize_fraud_flag(raw.get("fraud_flag")),
        "source_dataset": source_dataset,
        "source_row_index": source_row_index,
    }
