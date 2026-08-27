"""
Regression test: run the COMPLETE ingestion against the real 250K source and assert
every Day 1 audited invariant (Day 2A §14 "Regression test").

This is the test that proves the pipeline reproduces the audited dataset exactly, so it
deliberately uses the real file rather than a fixture. It takes ~25s.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from aventum_ingest.constants import (
    EXPECTED_AMOUNT_MAX,
    EXPECTED_AMOUNT_MIN,
    EXPECTED_DEVICE_DISTRIBUTION,
    EXPECTED_FRAUD_FLAG_TRUE_COUNT,
    EXPECTED_NETWORK_DISTRIBUTION,
    EXPECTED_PAYMENT_METHOD_DISTRIBUTION,
    EXPECTED_ROW_COUNT,
    EXPECTED_STATUS_DISTRIBUTION,
    VALID_BANKS,
    VALID_REGIONS,
)
from aventum_ingest.integrity import fingerprint_source
from aventum_ingest.pipeline import RunStatus, run_ingestion


@pytest.fixture(scope="module")
def module_engine(test_database_url):
    """
    Module-scoped engine so the 250K load runs once for the whole file.

    The shared function-scoped `engine` fixture truncates per test, which would force a
    ~25s re-ingestion for every assertion.
    """
    from sqlalchemy import text as _text

    from aventum_ingest.db import build_engine

    eng = build_engine(test_database_url)
    with eng.begin() as connection:
        connection.execute(
            _text(
                "TRUNCATE TABLE transactions, transactions_staging, "
                "ingestion_rejects, ingestion_runs RESTART IDENTITY CASCADE"
            )
        )
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def full_ingestion(module_engine, real_source_config):
    """Run the real ingestion once; every assertion below shares the result."""
    result = run_ingestion(module_engine, real_source_config, force=True)
    return module_engine, real_source_config, result


def _scalar(engine, sql: str, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _distribution(engine, column: str) -> dict[str, int]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(f"SELECT {column} AS k, COUNT(*) AS n FROM transactions GROUP BY {column}")
        ).all()
    return {row.k: row.n for row in rows}


def test_full_ingestion_succeeds_with_zero_rejects(full_ingestion):
    _, _, result = full_ingestion
    assert result.status == RunStatus.SUCCEEDED
    assert result.rows_read == EXPECTED_ROW_COUNT
    assert result.rows_valid == EXPECTED_ROW_COUNT
    assert result.rows_rejected == 0, (
        "Day 1 audited this source as fully clean; any rejection is a new, "
        "previously undocumented issue that must be investigated."
    )
    assert result.rows_inserted == EXPECTED_ROW_COUNT


def test_row_count_and_uniqueness(full_ingestion):
    engine, _, _ = full_ingestion
    assert _scalar(engine, "SELECT COUNT(*) FROM transactions") == EXPECTED_ROW_COUNT
    assert (
        _scalar(engine, "SELECT COUNT(DISTINCT transaction_id) FROM transactions")
        == EXPECTED_ROW_COUNT
    )


def test_no_duplicate_rows(full_ingestion):
    engine, _, _ = full_ingestion
    duplicates = _scalar(
        engine,
        """
        SELECT COALESCE(SUM(c - 1), 0) FROM (
            SELECT COUNT(*) AS c FROM transactions
            GROUP BY transaction_id, timestamp, amount, status, payment_method,
                     merchant_category, region, device, network,
                     sender_bank, receiver_bank, fraud_flag, source_dataset
            HAVING COUNT(*) > 1
        ) d
        """,
    )
    assert int(duplicates) == 0


def test_status_distribution_matches_day1(full_ingestion):
    engine, _, _ = full_ingestion
    assert _distribution(engine, "status") == EXPECTED_STATUS_DISTRIBUTION


def test_payment_method_distribution_matches_day1(full_ingestion):
    engine, _, _ = full_ingestion
    assert _distribution(engine, "payment_method") == EXPECTED_PAYMENT_METHOD_DISTRIBUTION


def test_device_distribution_matches_day1(full_ingestion):
    engine, _, _ = full_ingestion
    assert _distribution(engine, "device") == EXPECTED_DEVICE_DISTRIBUTION


def test_network_distribution_matches_day1(full_ingestion):
    engine, _, _ = full_ingestion
    assert _distribution(engine, "network") == EXPECTED_NETWORK_DISTRIBUTION


def test_amount_range_matches_day1(full_ingestion):
    engine, _, _ = full_ingestion
    assert _scalar(engine, "SELECT MIN(amount) FROM transactions") == EXPECTED_AMOUNT_MIN
    assert _scalar(engine, "SELECT MAX(amount) FROM transactions") == EXPECTED_AMOUNT_MAX


def test_all_expected_banks_present(full_ingestion):
    engine, _, _ = full_ingestion
    with engine.connect() as connection:
        senders = set(connection.execute(text("SELECT DISTINCT sender_bank FROM transactions")).scalars())
        receivers = set(connection.execute(text("SELECT DISTINCT receiver_bank FROM transactions")).scalars())
    assert senders == set(VALID_BANKS)
    assert receivers == set(VALID_BANKS)


def test_all_expected_states_present(full_ingestion):
    engine, _, _ = full_ingestion
    with engine.connect() as connection:
        regions = set(connection.execute(text("SELECT DISTINCT region FROM transactions")).scalars())
    assert regions == set(VALID_REGIONS)


def test_p2p_merchant_category_rule_holds_across_the_whole_dataset(full_ingestion):
    engine, _, _ = full_ingestion
    assert _scalar(
        engine,
        "SELECT COUNT(*) FROM transactions "
        "WHERE payment_method = 'P2P' AND merchant_category IS NOT NULL",
    ) == 0
    assert _scalar(
        engine,
        "SELECT COUNT(*) FROM transactions "
        "WHERE payment_method <> 'P2P' AND merchant_category IS NULL",
    ) == 0
    # Day 1 measured 112,445 P2P rows; every one must now carry a NULL category.
    assert _scalar(
        engine, "SELECT COUNT(*) FROM transactions WHERE merchant_category IS NULL"
    ) == EXPECTED_PAYMENT_METHOD_DISTRIBUTION["P2P"]


def test_timestamp_range_matches_day1_after_ist_assumption(full_ingestion):
    """Day 1 recorded the range in source-local (IST) terms; it must round-trip."""
    engine, _, _ = full_ingestion
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT MIN(timestamp AT TIME ZONE 'Asia/Kolkata') AS lo, "
                "       MAX(timestamp AT TIME ZONE 'Asia/Kolkata') AS hi FROM transactions"
            )
        ).mappings().one()
    assert str(row["lo"]) == "2024-01-01 00:05:10"
    assert str(row["hi"]) == "2024-12-30 23:55:40"


def test_fraud_flag_count_matches_day1(full_ingestion):
    engine, _, _ = full_ingestion
    assert (
        _scalar(engine, "SELECT COUNT(*) FROM transactions WHERE fraud_flag IS TRUE")
        == EXPECTED_FRAUD_FLAG_TRUE_COUNT
    )


def test_gmv_is_preserved(full_ingestion):
    """Day 1 measured total GMV of Rs 327,939,009 across the source."""
    engine, _, _ = full_ingestion
    assert _scalar(engine, "SELECT SUM(amount) FROM transactions") == Decimal("327939009.00")


def test_provenance_is_populated_on_every_row(full_ingestion):
    engine, _, result = full_ingestion
    assert _scalar(
        engine,
        "SELECT COUNT(*) FROM transactions "
        "WHERE source_dataset <> 'upi_transactions_2024' OR ingestion_run_id IS NULL",
    ) == 0
    assert _scalar(
        engine, "SELECT COUNT(*) FROM transactions WHERE ingestion_run_id <> :rid",
        rid=result.ingestion_run_id,
    ) == 0


def test_source_hash_recorded_matches_the_file_on_disk(full_ingestion):
    engine, config, result = full_ingestion
    on_disk = fingerprint_source(config.source_path).sha256
    recorded = _scalar(
        engine, "SELECT source_sha256 FROM ingestion_runs WHERE ingestion_run_id = :rid",
        rid=result.ingestion_run_id,
    )
    assert recorded == on_disk


def test_generated_alias_columns_hold_across_the_whole_dataset(full_ingestion):
    engine, _, _ = full_ingestion
    assert _scalar(
        engine,
        "SELECT COUNT(*) FROM transactions "
        "WHERE transaction_type <> payment_method OR issuer_bank <> sender_bank",
    ) == 0


def test_post_load_verification_reports_all_checks_passing(full_ingestion):
    _, _, result = full_ingestion
    assert result.verification is not None
    assert result.verification.passed, result.verification.summary()
    assert result.canonical_fingerprint
