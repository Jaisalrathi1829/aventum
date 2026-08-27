"""
Canonical-record validation, run BEFORE database promotion (Day 2A §7).

Every check here is also enforced by a PostgreSQL constraint (§8). The duplication is
deliberate defense in depth: this layer produces an inspectable, per-record quarantine
entry, while the database layer guarantees an invalid canonical state is impossible
even if this layer has a bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

from .constants import (
    EXPECTED_AMOUNT_MAX,
    EXPECTED_AMOUNT_MIN,
    EXPECTED_TIMESTAMP_MAX_IST,
    EXPECTED_TIMESTAMP_MIN_IST,
    P2P_PAYMENT_METHOD,
    VALID_BANKS,
    VALID_DEVICES,
    VALID_MERCHANT_CATEGORIES,
    VALID_NETWORKS,
    VALID_PAYMENT_METHODS,
    VALID_REGIONS,
    VALID_STATUSES,
)
from .normalize import IST, SOURCE_TIMESTAMP_FORMAT

# Audited source bounds, as tz-aware IST datetimes (computed once, module-level).
_EXPECTED_TS_MIN = datetime.strptime(
    EXPECTED_TIMESTAMP_MIN_IST, SOURCE_TIMESTAMP_FORMAT
).replace(tzinfo=IST)
_EXPECTED_TS_MAX = datetime.strptime(
    EXPECTED_TIMESTAMP_MAX_IST, SOURCE_TIMESTAMP_FORMAT
).replace(tzinfo=IST)


class ErrorCategory:
    """Stable category labels, used to group quarantined rows in the ingestion report."""

    IDENTITY = "identity"
    DUPLICATE_ID = "duplicate_id"
    TIMESTAMP = "timestamp"
    AMOUNT = "amount"
    STATUS = "status"
    PAYMENT_CONTEXT = "payment_context"
    BANKING = "banking"
    MERCHANT = "merchant"
    PROVENANCE = "provenance"
    NORMALIZATION = "normalization"


@dataclass(frozen=True)
class ValidationFailure:
    """One reason a record was rejected."""

    category: str
    message: str
    fields: tuple[str, ...]


def _check_identity(record: dict[str, Any]) -> list[ValidationFailure]:
    value = record.get("transaction_id")
    if not value or not str(value).strip():
        return [
            ValidationFailure(
                ErrorCategory.IDENTITY, "transaction_id is missing or empty", ("transaction_id",)
            )
        ]
    return []


def _check_timestamp(record: dict[str, Any]) -> list[ValidationFailure]:
    value = record.get("timestamp")
    if value is None:
        return [ValidationFailure(ErrorCategory.TIMESTAMP, "timestamp is null", ("timestamp",))]
    if not isinstance(value, datetime):
        return [
            ValidationFailure(
                ErrorCategory.TIMESTAMP, f"timestamp is not a datetime: {value!r}", ("timestamp",)
            )
        ]
    if value.tzinfo is None:
        return [
            ValidationFailure(
                ErrorCategory.TIMESTAMP,
                "timestamp is timezone-naive; the documented IST assumption was not applied",
                ("timestamp",),
            )
        ]
    if not (_EXPECTED_TS_MIN <= value <= _EXPECTED_TS_MAX):
        return [
            ValidationFailure(
                ErrorCategory.TIMESTAMP,
                f"timestamp {value.isoformat()} falls outside the Day 1 audited source range "
                f"[{_EXPECTED_TS_MIN.isoformat()}, {_EXPECTED_TS_MAX.isoformat()}]",
                ("timestamp",),
            )
        ]
    return []


def _check_amount(record: dict[str, Any]) -> list[ValidationFailure]:
    value = record.get("amount")
    if value is None:
        return [ValidationFailure(ErrorCategory.AMOUNT, "amount is null", ("amount",))]
    if not isinstance(value, Decimal):
        return [
            ValidationFailure(
                ErrorCategory.AMOUNT, f"amount is not numeric: {value!r}", ("amount",)
            )
        ]
    if value <= 0:
        return [
            ValidationFailure(
                ErrorCategory.AMOUNT, f"amount must be > 0, got {value}", ("amount",)
            )
        ]
    if not (EXPECTED_AMOUNT_MIN <= value <= EXPECTED_AMOUNT_MAX):
        return [
            ValidationFailure(
                ErrorCategory.AMOUNT,
                f"amount {value} falls outside the Day 1 audited range "
                f"[{EXPECTED_AMOUNT_MIN}, {EXPECTED_AMOUNT_MAX}]",
                ("amount",),
            )
        ]
    return []


def _check_status(record: dict[str, Any]) -> list[ValidationFailure]:
    value = record.get("status")
    if value not in VALID_STATUSES:
        return [
            ValidationFailure(
                ErrorCategory.STATUS,
                f"status {value!r} is not one of {sorted(VALID_STATUSES)}",
                ("status",),
            )
        ]
    return []


def _check_payment_context(record: dict[str, Any]) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []

    payment_method = record.get("payment_method")
    if payment_method not in VALID_PAYMENT_METHODS:
        failures.append(
            ValidationFailure(
                ErrorCategory.PAYMENT_CONTEXT,
                f"payment_method {payment_method!r} is not one of {sorted(VALID_PAYMENT_METHODS)}",
                ("payment_method",),
            )
        )

    device = record.get("device")
    if device not in VALID_DEVICES:
        failures.append(
            ValidationFailure(
                ErrorCategory.PAYMENT_CONTEXT,
                f"device {device!r} is not one of {sorted(VALID_DEVICES)}",
                ("device",),
            )
        )

    network = record.get("network")
    if network not in VALID_NETWORKS:
        failures.append(
            ValidationFailure(
                ErrorCategory.PAYMENT_CONTEXT,
                f"network {network!r} is not one of {sorted(VALID_NETWORKS)}",
                ("network",),
            )
        )

    region = record.get("region")
    if region not in VALID_REGIONS:
        failures.append(
            ValidationFailure(
                ErrorCategory.PAYMENT_CONTEXT,
                f"region {region!r} is not one of the {len(VALID_REGIONS)} audited states",
                ("region",),
            )
        )

    return failures


def _check_banking(record: dict[str, Any]) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    for field in ("sender_bank", "receiver_bank"):
        value = record.get(field)
        if not value:
            failures.append(
                ValidationFailure(ErrorCategory.BANKING, f"{field} is missing", (field,))
            )
        elif value not in VALID_BANKS:
            failures.append(
                ValidationFailure(
                    ErrorCategory.BANKING,
                    f"{field} {value!r} is outside the audited 8-bank source universe "
                    f"{sorted(VALID_BANKS)}",
                    (field,),
                )
            )
    return failures


def _check_merchant(record: dict[str, Any]) -> list[ValidationFailure]:
    """Enforce the P2P rule in both directions, plus category vocabulary."""
    payment_method = record.get("payment_method")
    category = record.get("merchant_category")

    if payment_method == P2P_PAYMENT_METHOD:
        if category is not None:
            return [
                ValidationFailure(
                    ErrorCategory.MERCHANT,
                    f"P2P row must have merchant_category NULL, got {category!r}",
                    ("merchant_category", "payment_method"),
                )
            ]
        return []

    if category is None:
        return [
            ValidationFailure(
                ErrorCategory.MERCHANT,
                f"non-P2P row ({payment_method!r}) must retain a merchant_category, got NULL",
                ("merchant_category", "payment_method"),
            )
        ]
    if category not in VALID_MERCHANT_CATEGORIES:
        return [
            ValidationFailure(
                ErrorCategory.MERCHANT,
                f"merchant_category {category!r} is not one of the 10 audited categories",
                ("merchant_category",),
            )
        ]
    return []


def _check_provenance(record: dict[str, Any]) -> list[ValidationFailure]:
    if not record.get("source_dataset"):
        return [
            ValidationFailure(
                ErrorCategory.PROVENANCE, "source_dataset is missing", ("source_dataset",)
            )
        ]
    if record.get("source_row_index") is None:
        return [
            ValidationFailure(
                ErrorCategory.PROVENANCE, "source_row_index is missing", ("source_row_index",)
            )
        ]
    return []


_RECORD_CHECKS = (
    _check_identity,
    _check_timestamp,
    _check_amount,
    _check_status,
    _check_payment_context,
    _check_banking,
    _check_merchant,
    _check_provenance,
)


def validate_record(record: dict[str, Any]) -> list[ValidationFailure]:
    """Run every per-record check and return all failures (not just the first)."""
    failures: list[ValidationFailure] = []
    for check in _RECORD_CHECKS:
        failures.extend(check(record))
    return failures


def find_duplicate_ids(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    """
    Count occurrences of each transaction_id.

    Uniqueness is a dataset-level property, so it cannot be decided by validate_record
    alone; the pipeline calls this across the whole batch before staging.
    """
    counts: dict[str, int] = {}
    for record in records:
        key = record.get("transaction_id")
        if key is not None:
            counts[key] = counts.get(key, 0) + 1
    return {key: count for key, count in counts.items() if count > 1}
