"""
Database-level defense in depth (Day 2A §8).

These tests bypass the ETL entirely and write straight to PostgreSQL, proving the
database rejects invalid canonical states even if application validation had a bug.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

BASE_ROW = {
    "transaction_id": "TXN-DB-1",
    "timestamp": "2024-06-15 12:30:45+05:30",
    "amount": "1500.00",
    "status": "SUCCESS",
    "payment_method": "P2M",
    "merchant_category": "Grocery",
    "region": "Maharashtra",
    "device": "Android",
    "network": "4G",
    "sender_bank": "SBI",
    "receiver_bank": "HDFC",
    "fraud_flag": False,
    "source_dataset": "upi_transactions_2024",
}

INSERT_SQL = text(
    """
    INSERT INTO transactions (
        transaction_id, timestamp, amount, status, payment_method, merchant_category,
        region, device, network, sender_bank, receiver_bank, fraud_flag,
        source_dataset, ingestion_run_id
    ) VALUES (
        :transaction_id, :timestamp, :amount, :status, :payment_method, :merchant_category,
        :region, :device, :network, :sender_bank, :receiver_bank, :fraud_flag,
        :source_dataset, :ingestion_run_id
    )
    """
)


@pytest.fixture()
def run_id(engine) -> int:
    """A minimal ingestion_runs row to satisfy the provenance FK."""
    with engine.begin() as connection:
        return connection.execute(
            text(
                """
                INSERT INTO ingestion_runs (
                    source_file, source_filename, source_sha256, source_size_bytes,
                    source_dataset, schema_version, code_version, status
                ) VALUES ('t', 't', :sha, 1, 'upi_transactions_2024', '1.0.0', '0.1.0', 'RUNNING')
                RETURNING ingestion_run_id
                """
            ),
            {"sha": "a" * 64},
        ).scalar_one()


def _insert(engine, run_id: int, **overrides):
    row = dict(BASE_ROW)
    row.update(overrides)
    row["ingestion_run_id"] = overrides.get("ingestion_run_id", run_id)
    with engine.begin() as connection:
        connection.execute(INSERT_SQL, row)


def test_valid_row_inserts(engine, run_id):
    _insert(engine, run_id)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM transactions")).scalar_one() == 1


# --------------------------------------------------------------------------
# Primary key
# --------------------------------------------------------------------------

def test_primary_key_rejects_duplicate_transaction_id(engine, run_id):
    _insert(engine, run_id, transaction_id="DUP")
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id="DUP")


# --------------------------------------------------------------------------
# Foreign keys
# --------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["sender_bank", "receiver_bank"])
def test_foreign_key_rejects_unknown_bank(engine, run_id, field):
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id=f"FK-{field}", **{field: "Barclays"})


def test_foreign_key_rejects_unknown_ingestion_run(engine, run_id):
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id="FK-RUN", ingestion_run_id=999_999)


def test_banks_dimension_is_seeded_by_migration(engine):
    with engine.connect() as connection:
        codes = set(connection.execute(text("SELECT bank_code FROM banks")).scalars())
    assert codes == {"SBI", "HDFC", "ICICI", "IndusInd", "Axis", "PNB", "Yes Bank", "Kotak"}


# --------------------------------------------------------------------------
# CHECK constraints
# --------------------------------------------------------------------------

@pytest.mark.parametrize("amount", ["0", "-1", "-1500.00"])
def test_check_rejects_non_positive_amount(engine, run_id, amount):
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id=f"AMT-{amount}", amount=amount)


@pytest.mark.parametrize("status", ["PENDING", "success", "TIMEOUT"])
def test_check_rejects_invalid_status(engine, run_id, status):
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id=f"ST-{status}", status=status)


def test_check_rejects_invalid_payment_method(engine, run_id):
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id="PM", payment_method="Subscription")


def test_check_rejects_invalid_device(engine, run_id):
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id="DEV", device="Tablet")


def test_check_rejects_invalid_network(engine, run_id):
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id="NET", network="2G")


def test_check_rejects_region_outside_audited_states(engine, run_id):
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id="REG", region="Kerala")


def test_check_rejects_empty_transaction_id(engine, run_id):
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id="")


# --------------------------------------------------------------------------
# P2P constraint (both directions)
# --------------------------------------------------------------------------

def test_check_rejects_p2p_row_with_merchant_category(engine, run_id):
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id="P2P-BAD",
                payment_method="P2P", merchant_category="Grocery")


def test_check_accepts_p2p_row_without_merchant_category(engine, run_id):
    _insert(engine, run_id, transaction_id="P2P-OK",
            payment_method="P2P", merchant_category=None)


def test_check_rejects_non_p2p_row_without_merchant_category(engine, run_id):
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id="P2M-BAD",
                payment_method="P2M", merchant_category=None)


# --------------------------------------------------------------------------
# NOT NULL
# --------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["timestamp", "amount", "status", "payment_method",
                                   "region", "device", "network",
                                   "sender_bank", "receiver_bank", "source_dataset"])
def test_not_null_is_enforced(engine, run_id, field):
    with pytest.raises(IntegrityError):
        _insert(engine, run_id, transaction_id=f"NN-{field}", **{field: None})


# --------------------------------------------------------------------------
# Generated alias columns (the documented schema deviation)
# --------------------------------------------------------------------------

def test_generated_alias_columns_mirror_their_sources(engine, run_id):
    _insert(engine, run_id, transaction_id="GEN-1", payment_method="Recharge",
            merchant_category="Utilities", sender_bank="Kotak")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT payment_method, transaction_type, sender_bank, issuer_bank "
                "FROM transactions WHERE transaction_id = 'GEN-1'"
            )
        ).mappings().one()
    assert row["transaction_type"] == row["payment_method"] == "Recharge"
    assert row["issuer_bank"] == row["sender_bank"] == "Kotak"


def test_generated_alias_columns_cannot_be_written_directly(engine, run_id):
    """A generated column is structurally un-divergeable: PostgreSQL refuses the write."""
    with pytest.raises(Exception):
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO transactions (transaction_id, transaction_type) "
                     "VALUES ('X', 'P2P')")
            )


# --------------------------------------------------------------------------
# ingestion_runs invariants
# --------------------------------------------------------------------------

def test_ingestion_run_row_accounting_is_enforced(engine):
    """A finished run must account for every row it read."""
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO ingestion_runs (
                        source_file, source_filename, source_sha256, source_size_bytes,
                        source_dataset, schema_version, code_version, status,
                        rows_read, rows_valid, rows_rejected
                    ) VALUES ('t','t',:sha,1,'upi_transactions_2024','1.0.0','0.1.0',
                              'SUCCEEDED', 100, 50, 10)
                    """
                ),
                {"sha": "b" * 64},
            )


def test_ingestion_run_status_vocabulary_is_enforced(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO ingestion_runs (
                        source_file, source_filename, source_sha256, source_size_bytes,
                        source_dataset, schema_version, code_version, status
                    ) VALUES ('t','t',:sha,1,'upi_transactions_2024','1.0.0','0.1.0','WEIRD')
                    """
                ),
                {"sha": "c" * 64},
            )


def test_canonical_view_exposes_issuer_bank_full_name(engine, run_id):
    """issuer_bank_full_name is served by join, not denormalized onto transactions."""
    _insert(engine, run_id, transaction_id="VIEW-1", sender_bank="PNB")
    with engine.connect() as connection:
        value = connection.execute(
            text("SELECT issuer_bank_full_name FROM v_transactions_canonical "
                 "WHERE transaction_id = 'VIEW-1'")
        ).scalar_one()
    assert value == "Punjab National Bank"
