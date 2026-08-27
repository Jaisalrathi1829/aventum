"""Unit tests: field mapping, normalization, timestamp handling, P2P rule."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from aventum_ingest.normalize import (
    IST,
    NormalizationError,
    normalize_amount,
    normalize_fraud_flag,
    normalize_merchant_category,
    normalize_row,
    normalize_status,
    normalize_timestamp,
    normalize_transaction_id,
)
from tests.conftest import TEST_DATASET, VALID_ROW, make_row


# --------------------------------------------------------------------------
# Field mapping
# --------------------------------------------------------------------------

def test_normalize_row_maps_every_documented_source_column():
    """Each documented source column lands on its canonical field."""
    record = normalize_row(VALID_ROW, source_row_index=0, source_dataset=TEST_DATASET)

    assert record["transaction_id"] == "TXN0000000001"       # 'transaction id'
    assert record["amount"] == Decimal("1500.00")            # 'amount (INR)'
    assert record["status"] == "SUCCESS"                     # 'transaction_status'
    assert record["payment_method"] == "P2M"                 # 'transaction type'
    assert record["merchant_category"] == "Grocery"
    assert record["region"] == "Maharashtra"                 # 'sender_state'
    assert record["device"] == "Android"                     # 'device_type'
    assert record["network"] == "4G"                         # 'network_type'
    assert record["sender_bank"] == "SBI"
    assert record["receiver_bank"] == "HDFC"
    assert record["fraud_flag"] is False
    assert record["source_dataset"] == TEST_DATASET
    assert record["source_row_index"] == 0


def test_unmapped_source_columns_are_not_carried_into_canonical_record():
    """Age/hour/day/weekend columns are deliberately dropped, not silently retained."""
    record = normalize_row(VALID_ROW, source_row_index=0, source_dataset=TEST_DATASET)
    for dropped in ("sender_age_group", "receiver_age_group", "hour_of_day",
                    "day_of_week", "is_weekend"):
        assert dropped not in record


def test_normalization_is_deterministic():
    """Same input row normalizes identically every time."""
    assert normalize_row(VALID_ROW, 0, TEST_DATASET) == normalize_row(VALID_ROW, 0, TEST_DATASET)


# --------------------------------------------------------------------------
# Transaction id
# --------------------------------------------------------------------------

def test_transaction_id_is_whitespace_trimmed():
    assert normalize_transaction_id("  TXN123  ") == "TXN123"


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_missing_transaction_id_raises(bad):
    with pytest.raises(NormalizationError) as exc:
        normalize_transaction_id(bad)
    assert exc.value.field == "transaction_id"


# --------------------------------------------------------------------------
# Timestamp (documented IST assumption)
# --------------------------------------------------------------------------

def test_timestamp_gets_documented_ist_offset():
    result = normalize_timestamp("2024-06-15 12:30:45")
    assert result.tzinfo is not None
    assert result.utcoffset().total_seconds() == 5.5 * 3600
    assert (result.year, result.month, result.day) == (2024, 6, 15)
    assert (result.hour, result.minute, result.second) == (12, 30, 45)


def test_timestamp_converts_to_expected_utc_instant():
    """IST 12:30:45 is 07:00:45 UTC -- the assumption must shift the instant."""
    result = normalize_timestamp("2024-06-15 12:30:45").astimezone(timezone.utc)
    assert result == datetime(2024, 6, 15, 7, 0, 45, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "bad",
    ["2024-13-01 00:00:00", "15/06/2024 12:30:45", "not-a-timestamp", "2024-06-15", ""],
)
def test_malformed_timestamp_is_rejected(bad):
    with pytest.raises(NormalizationError) as exc:
        normalize_timestamp(bad)
    assert exc.value.field == "timestamp"


def test_timezone_aware_source_timestamp_is_rejected():
    """A tz-aware source value means the source changed; the IST assumption would be ambiguous."""
    aware = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(NormalizationError):
        normalize_timestamp(aware)


def test_ambiguous_date_is_not_silently_reinterpreted():
    """DD/MM vs MM/DD guessing must not happen -- the explicit format rejects it."""
    with pytest.raises(NormalizationError):
        normalize_timestamp("06/15/2024 12:30:45")


# --------------------------------------------------------------------------
# Amount
# --------------------------------------------------------------------------

def test_amount_becomes_decimal_at_canonical_scale():
    assert normalize_amount("1500") == Decimal("1500.00")
    assert normalize_amount(42099) == Decimal("42099.00")


def test_amount_avoids_binary_float_error():
    """Monetary values must not inherit float representation error."""
    assert normalize_amount(0.1 + 0.2) == Decimal("0.30")


@pytest.mark.parametrize("bad", ["abc", "", None, "1,500"])
def test_non_numeric_amount_raises(bad):
    with pytest.raises(NormalizationError) as exc:
        normalize_amount(bad)
    assert exc.value.field == "amount"


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("SUCCESS", "SUCCESS"),
    ("success", "SUCCESS"),
    ("  Failed  ", "FAILED"),
])
def test_status_canonicalizes_to_uppercase(raw, expected):
    assert normalize_status(raw) == expected


def test_status_does_not_invent_membership():
    """Canonicalization upper-cases but never maps an unknown value onto a valid one."""
    assert normalize_status("cancelled") == "CANCELLED"  # validation rejects it later


# --------------------------------------------------------------------------
# P2P merchant-category rule
# --------------------------------------------------------------------------

def test_p2p_merchant_category_is_nulled():
    """The documented cleaning rule: P2P rows carry no merchant."""
    assert normalize_merchant_category("Grocery", "P2P") is None


@pytest.mark.parametrize("method", ["P2M", "Bill Payment", "Recharge"])
def test_non_p2p_merchant_category_is_preserved(method):
    assert normalize_merchant_category("Grocery", method) == "Grocery"


def test_p2p_rule_applies_through_full_row_normalization():
    record = normalize_row(make_row(1, **{"transaction type": "P2P"}), 0, TEST_DATASET)
    assert record["payment_method"] == "P2P"
    assert record["merchant_category"] is None


def test_merchant_information_is_never_invented_for_missing_values():
    """A blank non-P2P category stays None; the pipeline must not fabricate one."""
    assert normalize_merchant_category("", "P2M") is None


# --------------------------------------------------------------------------
# Bank normalization
# --------------------------------------------------------------------------

def test_bank_values_are_trimmed_but_not_case_folded():
    """Casing is meaningful for bank codes; only surrounding whitespace is removed."""
    record = normalize_row(make_row(1, sender_bank="  Yes Bank  ", receiver_bank=" Kotak "), 0, TEST_DATASET)
    assert record["sender_bank"] == "Yes Bank"
    assert record["receiver_bank"] == "Kotak"


def test_missing_bank_raises():
    with pytest.raises(NormalizationError) as exc:
        normalize_row(make_row(1, sender_bank=""), 0, TEST_DATASET)
    assert exc.value.field == "sender_bank"


# --------------------------------------------------------------------------
# fraud_flag
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("0", False), ("1", True), ("true", True), ("False", False), (0, False), (1, True),
])
def test_fraud_flag_parsing(raw, expected):
    assert normalize_fraud_flag(raw) is expected


def test_unrecognized_fraud_flag_raises():
    with pytest.raises(NormalizationError):
        normalize_fraud_flag("maybe")
