"""Validation tests: every rejection rule required by Day 2A §7."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from aventum_ingest.normalize import IST, normalize_row
from aventum_ingest.validate import (
    ErrorCategory,
    find_duplicate_ids,
    validate_record,
)
from tests.conftest import TEST_DATASET, VALID_ROW, make_row


def _record(**overrides) -> dict:
    """A valid canonical record with optional post-normalization overrides."""
    record = normalize_row(VALID_ROW, 0, TEST_DATASET)
    record.update(overrides)
    return record


def _categories(record: dict) -> set[str]:
    return {failure.category for failure in validate_record(record)}


def test_valid_record_passes():
    assert validate_record(_record()) == []


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", None])
def test_missing_transaction_id_is_rejected(bad):
    assert ErrorCategory.IDENTITY in _categories(_record(transaction_id=bad))


def test_duplicate_transaction_ids_are_detected():
    records = [_record(transaction_id="A"), _record(transaction_id="A"),
               _record(transaction_id="B")]
    assert find_duplicate_ids(records) == {"A": 2}


def test_no_duplicates_returns_empty():
    records = [_record(transaction_id="A"), _record(transaction_id="B")]
    assert find_duplicate_ids(records) == {}


# --------------------------------------------------------------------------
# Timestamp
# --------------------------------------------------------------------------

def test_null_timestamp_is_rejected():
    assert ErrorCategory.TIMESTAMP in _categories(_record(timestamp=None))


def test_naive_timestamp_is_rejected():
    """The IST assumption must have been applied before validation."""
    assert ErrorCategory.TIMESTAMP in _categories(
        _record(timestamp=datetime(2024, 6, 15, 12, 0, 0))
    )


@pytest.mark.parametrize("out_of_range", [
    datetime(2023, 12, 31, 23, 59, 59, tzinfo=IST),   # before audited min
    datetime(2025, 1, 1, 0, 0, 0, tzinfo=IST),        # after audited max
])
def test_timestamp_outside_audited_range_is_rejected(out_of_range):
    assert ErrorCategory.TIMESTAMP in _categories(_record(timestamp=out_of_range))


def test_timestamp_at_audited_boundary_is_accepted():
    boundary = datetime(2024, 1, 1, 0, 5, 10, tzinfo=IST)
    assert validate_record(_record(timestamp=boundary)) == []


# --------------------------------------------------------------------------
# Amount
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-1"), Decimal("-1500.00")])
def test_non_positive_amount_is_rejected(bad):
    assert ErrorCategory.AMOUNT in _categories(_record(amount=bad))


def test_null_amount_is_rejected():
    assert ErrorCategory.AMOUNT in _categories(_record(amount=None))


@pytest.mark.parametrize("bad", [Decimal("9"), Decimal("42100")])
def test_amount_outside_audited_range_is_rejected(bad):
    assert ErrorCategory.AMOUNT in _categories(_record(amount=bad))


@pytest.mark.parametrize("ok", [Decimal("10"), Decimal("42099")])
def test_amount_at_audited_boundary_is_accepted(ok):
    assert validate_record(_record(amount=ok)) == []


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["PENDING", "TIMEOUT", "REVERSED", "success", "", None])
def test_invalid_status_is_rejected(bad):
    assert ErrorCategory.STATUS in _categories(_record(status=bad))


@pytest.mark.parametrize("ok", ["SUCCESS", "FAILED"])
def test_valid_status_is_accepted(ok):
    assert validate_record(_record(status=ok)) == []


# --------------------------------------------------------------------------
# Payment context vocabularies
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["Tablet", "android", "", None])
def test_invalid_device_is_rejected(bad):
    assert ErrorCategory.PAYMENT_CONTEXT in _categories(_record(device=bad))


@pytest.mark.parametrize("bad", ["2G", "LTE", "wifi", "", None])
def test_invalid_network_is_rejected(bad):
    assert ErrorCategory.PAYMENT_CONTEXT in _categories(_record(network=bad))


@pytest.mark.parametrize("bad", ["Subscription", "p2p", "", None])
def test_invalid_payment_method_is_rejected(bad):
    assert ErrorCategory.PAYMENT_CONTEXT in _categories(_record(payment_method=bad))


def test_region_outside_audited_ten_states_is_rejected():
    """Kerala is a real Indian state but is NOT in this dataset's audited universe."""
    assert ErrorCategory.PAYMENT_CONTEXT in _categories(_record(region="Kerala"))


# --------------------------------------------------------------------------
# Banking
# --------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["sender_bank", "receiver_bank"])
def test_bank_outside_audited_universe_is_rejected(field):
    assert ErrorCategory.BANKING in _categories(_record(**{field: "Barclays"}))


@pytest.mark.parametrize("field", ["sender_bank", "receiver_bank"])
def test_missing_bank_is_rejected(field):
    assert ErrorCategory.BANKING in _categories(_record(**{field: None}))


def test_full_bank_name_is_not_accepted_as_bank_code():
    """`banks.legal_name` is not a valid bank_code -- the vocabularies are distinct."""
    assert ErrorCategory.BANKING in _categories(_record(sender_bank="State Bank Of India"))


# --------------------------------------------------------------------------
# Merchant / P2P rule (both directions)
# --------------------------------------------------------------------------

def test_p2p_row_with_merchant_category_is_rejected():
    record = _record(payment_method="P2P", merchant_category="Grocery")
    assert ErrorCategory.MERCHANT in _categories(record)


def test_p2p_row_with_null_merchant_category_is_accepted():
    assert validate_record(_record(payment_method="P2P", merchant_category=None)) == []


def test_non_p2p_row_without_merchant_category_is_rejected():
    record = _record(payment_method="P2M", merchant_category=None)
    assert ErrorCategory.MERCHANT in _categories(record)


def test_unknown_merchant_category_is_rejected():
    assert ErrorCategory.MERCHANT in _categories(_record(merchant_category="Crypto"))


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["source_dataset", "source_row_index"])
def test_missing_provenance_is_rejected(field):
    assert ErrorCategory.PROVENANCE in _categories(_record(**{field: None}))


# --------------------------------------------------------------------------
# Multiple failures
# --------------------------------------------------------------------------

def test_all_failures_are_reported_not_just_the_first():
    """An engineer inspecting quarantine should see every reason at once."""
    record = _record(status="BOGUS", device="Tablet", amount=Decimal("-5"))
    categories = _categories(record)
    assert {ErrorCategory.STATUS, ErrorCategory.PAYMENT_CONTEXT,
            ErrorCategory.AMOUNT} <= categories
