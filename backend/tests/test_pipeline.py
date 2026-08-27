"""
Pipeline tests: success, quarantine, drift failure, idempotency, failed-run recovery,
and atomicity / no-partial-promotion (Day 2A §14).
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import text

from aventum_ingest import pipeline as pipeline_module
from aventum_ingest.config import Config
from aventum_ingest.constants import EXPECTED_SOURCE_COLUMNS
from aventum_ingest.integrity import SourceIntegrityError, fingerprint_source
from aventum_ingest.pipeline import IngestionError, RunStatus, run_ingestion
from aventum_ingest.source_schema import SchemaDriftError
from aventum_ingest.validate import ErrorCategory
from tests.conftest import make_row


def _rows(count: int, **overrides) -> list[dict]:
    return [make_row(i + 1, **overrides) for i in range(count)]


def _count(engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def _run(engine, config: Config, **kwargs):
    return run_ingestion(engine, config, **kwargs)


# --------------------------------------------------------------------------
# Successful ingestion
# --------------------------------------------------------------------------

def test_successful_ingestion_loads_all_rows(engine, registered_source):
    config = registered_source(_rows(50))
    result = _run(engine, config)

    assert result.status == RunStatus.SUCCEEDED
    assert (result.rows_read, result.rows_valid, result.rows_rejected) == (50, 50, 0)
    assert result.rows_inserted == 50
    assert _count(engine, "transactions") == 50


def test_successful_ingestion_records_full_audit_metadata(engine, registered_source):
    config = registered_source(_rows(10))
    result = _run(engine, config)

    with engine.connect() as connection:
        run = connection.execute(
            text("SELECT * FROM ingestion_runs WHERE ingestion_run_id = :rid"),
            {"rid": result.ingestion_run_id},
        ).mappings().one()

    expected_sha = fingerprint_source(config.source_path).sha256
    assert run["source_sha256"] == expected_sha
    assert run["source_size_bytes"] == config.source_path.stat().st_size
    assert run["source_filename"] == config.source_path.name
    assert run["status"] == RunStatus.SUCCEEDED
    assert run["schema_version"] and run["code_version"]
    assert run["started_at"] is not None and run["finished_at"] is not None
    assert run["duration_seconds"] is not None
    assert run["canonical_fingerprint"]
    assert "IST" in run["timestamp_assumption"]
    assert run["schema_drift_report"]["is_fatal"] is False


def test_every_transaction_is_attributed_to_its_ingestion_run(engine, registered_source):
    result = _run(engine, registered_source(_rows(20)))
    with engine.connect() as connection:
        mismatched = connection.execute(
            text("SELECT COUNT(*) FROM transactions WHERE ingestion_run_id <> :rid"),
            {"rid": result.ingestion_run_id},
        ).scalar_one()
    assert mismatched == 0


def test_p2p_rule_holds_after_load(engine, registered_source):
    rows = _rows(10) + [
        make_row(100 + i, **{"transaction type": "P2P", "merchant_category": "Grocery"})
        for i in range(10)
    ]
    _run(engine, registered_source(rows))

    with engine.connect() as connection:
        violations = connection.execute(
            text(
                "SELECT COUNT(*) FROM transactions "
                "WHERE (payment_method = 'P2P' AND merchant_category IS NOT NULL) "
                "   OR (payment_method <> 'P2P' AND merchant_category IS NULL)"
            )
        ).scalar_one()
    assert violations == 0


# --------------------------------------------------------------------------
# Zero-row rejection path
# --------------------------------------------------------------------------

def test_clean_source_produces_zero_rejects(engine, registered_source):
    result = _run(engine, registered_source(_rows(100)))
    assert result.rows_rejected == 0
    assert _count(engine, "ingestion_rejects") == 0


# --------------------------------------------------------------------------
# Quarantine
# --------------------------------------------------------------------------

def test_invalid_rows_are_quarantined_not_discarded(engine, registered_source):
    rows = _rows(5) + [
        make_row(90, transaction_status="PENDING"),      # invalid status
        make_row(91, **{"amount (INR)": "-100"}),        # invalid amount
        make_row(92, device_type="Tablet"),              # invalid device
    ]
    result = _run(engine, registered_source(rows))

    assert result.rows_read == 8
    assert result.rows_valid == 5
    assert result.rows_rejected == 3
    assert _count(engine, "transactions") == 5
    assert _count(engine, "ingestion_rejects") == 3


def test_quarantine_records_are_inspectable(engine, registered_source):
    rows = _rows(2) + [make_row(90, transaction_status="PENDING")]
    result = _run(engine, registered_source(rows))

    with engine.connect() as connection:
        reject = connection.execute(
            text("SELECT * FROM ingestion_rejects WHERE ingestion_run_id = :rid"),
            {"rid": result.ingestion_run_id},
        ).mappings().one()

    assert reject["source_row_index"] == 2          # 0-based, header excluded
    assert reject["transaction_id"] == "TXN0000000090"
    assert reject["error_category"] == ErrorCategory.STATUS
    assert "PENDING" in reject["validation_error"]
    assert "status" in reject["offending_fields"]
    assert reject["raw_record"]["transaction_status"] == "PENDING"   # full payload kept
    assert reject["rejected_at"] is not None


def test_duplicate_transaction_ids_are_quarantined(engine, registered_source):
    rows = [make_row(1), make_row(1), make_row(2)]   # TXN...01 appears twice
    result = _run(engine, registered_source(rows))

    assert result.rows_valid == 1
    assert result.rows_rejected == 2
    assert _count(engine, "transactions") == 1

    with engine.connect() as connection:
        categories = set(
            connection.execute(text("SELECT error_category FROM ingestion_rejects")).scalars()
        )
    assert categories == {ErrorCategory.DUPLICATE_ID}


def test_malformed_timestamp_is_quarantined(engine, registered_source):
    rows = _rows(2) + [make_row(90, timestamp="not-a-timestamp")]
    result = _run(engine, registered_source(rows))

    assert result.rows_rejected == 1
    with engine.connect() as connection:
        category = connection.execute(
            text("SELECT error_category FROM ingestion_rejects")
        ).scalar_one()
    assert category == ErrorCategory.NORMALIZATION


# --------------------------------------------------------------------------
# Source integrity / schema drift
# --------------------------------------------------------------------------

def test_missing_source_file_fails_before_any_db_write(engine, tmp_path):
    config = Config(
        database_url="x", source_path=tmp_path / "nope.csv", project_root=tmp_path
    )
    with pytest.raises(SourceIntegrityError):
        _run(engine, config)
    assert _count(engine, "ingestion_runs") == 0
    assert _count(engine, "transactions") == 0


def test_empty_source_file_is_rejected(engine, tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    config = Config(database_url="x", source_path=empty, project_root=tmp_path)
    with pytest.raises(SourceIntegrityError):
        _run(engine, config)


def test_missing_required_column_aborts_before_canonical_mutation(engine, source_factory):
    columns = tuple(c for c in EXPECTED_SOURCE_COLUMNS if c != "transaction_status")
    config = source_factory(_rows(5), columns=columns)

    with pytest.raises(SchemaDriftError) as exc:
        _run(engine, config)

    assert "transaction_status" in str(exc.value)
    assert _count(engine, "transactions") == 0
    assert _count(engine, "ingestion_runs") == 0   # aborted before the run was opened


def test_renamed_column_is_not_silently_adapted_to(engine, source_factory):
    columns = tuple(
        "txn_id" if c == "transaction id" else c for c in EXPECTED_SOURCE_COLUMNS
    )
    config = source_factory(_rows(5), columns=columns)
    with pytest.raises(SchemaDriftError):
        _run(engine, config)


def test_existing_canonical_data_survives_a_drift_failure(engine, registered_source, source_factory):
    _run(engine, registered_source(_rows(30), name="good.csv"))
    before = _count(engine, "transactions")

    bad_columns = tuple(c for c in EXPECTED_SOURCE_COLUMNS if c != "sender_bank")
    with pytest.raises(SchemaDriftError):
        _run(engine, source_factory(_rows(5), columns=bad_columns, name="bad.csv"))

    assert _count(engine, "transactions") == before == 30


def test_unexpected_extra_column_is_tolerated_but_reported(engine, registered_source):
    """A new source column is non-fatal: it cannot corrupt the documented mapping."""
    columns = EXPECTED_SOURCE_COLUMNS + ("some_new_column",)
    result = _run(engine, registered_source(_rows(5), columns=columns))

    assert result.status == RunStatus.SUCCEEDED
    assert "some_new_column" in result.schema_drift.unexpected


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------

def test_rerunning_identical_source_does_not_duplicate(engine, registered_source):
    config = registered_source(_rows(40))
    first = _run(engine, config)
    second = _run(engine, config)

    assert first.status == RunStatus.SUCCEEDED
    assert second.status == RunStatus.SKIPPED_IDEMPOTENT
    assert second.rows_inserted == 0
    assert _count(engine, "transactions") == 40


def test_idempotent_skip_is_recorded_as_its_own_auditable_run(engine, registered_source):
    config = registered_source(_rows(10))
    _run(engine, config)
    second = _run(engine, config)

    with engine.connect() as connection:
        run = connection.execute(
            text("SELECT status, notes FROM ingestion_runs WHERE ingestion_run_id = :rid"),
            {"rid": second.ingestion_run_id},
        ).mappings().one()

    assert run["status"] == RunStatus.SKIPPED_IDEMPOTENT
    assert "already ingested" in run["notes"]
    assert _count(engine, "ingestion_runs") == 2   # both attempts are visible


def test_forced_rerun_converges_on_identical_content(engine, registered_source):
    config = registered_source(_rows(40))
    first = _run(engine, config)
    forced = _run(engine, config, force=True)

    assert forced.status == RunStatus.SUCCEEDED
    assert _count(engine, "transactions") == 40                      # not 80
    assert forced.canonical_fingerprint == first.canonical_fingerprint


def test_fingerprint_is_deterministic_across_independent_runs(engine, registered_source):
    """Same bytes + same code => same canonical fingerprint (Day 2A §4)."""
    first = _run(engine, registered_source(_rows(25), name="a.csv"))
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE transactions CASCADE"))
    second = _run(engine, registered_source(_rows(25), name="b.csv"), force=True)

    assert first.canonical_fingerprint == second.canonical_fingerprint


def test_changed_source_is_not_treated_as_already_ingested(engine, registered_source):
    """
    A different file must not be mistaken for an already-ingested one.

    REWRITTEN as part of the P1-1 fix. The previous version asserted that a second,
    different file simply *replaced* the first ("replaced, not appended") without ever
    checking provenance -- which encoded the P1-1 defect as expected behaviour. What
    actually matters is that each file keeps its OWN dataset identity and that neither
    silently displaces the other.
    """
    # Disjoint id ranges: `transaction_id` is a GLOBAL primary key, so two datasets can
    # only coexist when their identifiers do not collide.
    first_rows = [make_row(i + 1) for i in range(10)]
    second_rows = [make_row(500 + i) for i in range(12)]

    first = _run(engine, registered_source(first_rows, name="v1.csv"))
    second = _run(
        engine,
        registered_source(second_rows, name="v2.csv", dataset_name="test_fixture_dataset_v2"),
    )

    # Different bytes => a real ingestion, not an idempotent skip.
    assert first.status == RunStatus.SUCCEEDED
    assert second.status == RunStatus.SUCCEEDED
    assert first.source.sha256 != second.source.sha256

    # Each resolves to its OWN registered identity -- neither inherits the other's name.
    assert first.identity.dataset_name == "test_fixture_dataset"
    assert second.identity.dataset_name == "test_fixture_dataset_v2"

    # A distinct dataset does NOT delete another dataset's rows: both coexist, each
    # labelled with the identity that actually produced it.
    with engine.connect() as connection:
        by_dataset = dict(
            connection.execute(
                text("SELECT source_dataset, COUNT(*) FROM transactions GROUP BY source_dataset")
            ).all()
        )
    assert by_dataset == {"test_fixture_dataset": 10, "test_fixture_dataset_v2": 12}


# --------------------------------------------------------------------------
# Failed-run recovery
# --------------------------------------------------------------------------

def test_retry_after_a_failed_run_succeeds(engine, registered_source, monkeypatch):
    config = registered_source(_rows(30))

    def boom(*args, **kwargs):
        raise RuntimeError("simulated staging failure")

    monkeypatch.setattr(pipeline_module, "_verify_staging", boom)
    with pytest.raises(RuntimeError):
        _run(engine, config)

    assert _count(engine, "transactions") == 0

    monkeypatch.undo()
    result = _run(engine, config)      # a FAILED prior run must not block the retry

    assert result.status == RunStatus.SUCCEEDED
    assert _count(engine, "transactions") == 30


def test_failed_run_is_recorded_with_its_error(engine, registered_source, monkeypatch):
    config = registered_source(_rows(10))
    monkeypatch.setattr(
        pipeline_module, "_verify_staging",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated failure")),
    )
    with pytest.raises(RuntimeError):
        _run(engine, config)

    with engine.connect() as connection:
        run = connection.execute(
            text("SELECT status, error_message FROM ingestion_runs ORDER BY 1 DESC LIMIT 1")
        ).mappings().one()

    assert run["status"] == RunStatus.FAILED
    assert "simulated failure" in run["error_message"]


# --------------------------------------------------------------------------
# Atomicity / no partial promotion
# --------------------------------------------------------------------------

def test_failure_during_staging_leaves_canonical_table_untouched(
    engine, registered_source, monkeypatch
):
    _run(engine, registered_source(_rows(30), name="first.csv"))
    with engine.connect() as connection:
        before = connection.execute(
            text("SELECT COUNT(*) FROM transactions")
        ).scalar_one()

    monkeypatch.setattr(
        pipeline_module, "_verify_staging",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("staging verification failed")),
    )
    with pytest.raises(RuntimeError):
        _run(engine, registered_source(_rows(99), name="second.csv", dataset_name="test_fixture_dataset_second"))

    assert _count(engine, "transactions") == before == 30


def test_failure_after_delete_rolls_back_the_whole_promotion(
    engine, registered_source, monkeypatch
):
    """
    The hard case: the promotion transaction has already DELETEd the old rows when the
    INSERT fails. The transaction must roll back so the old rows are still there --
    there must be no window where the canonical table is empty or half-loaded.

    To reach that window we corrupt staging *after* the COPY, in place of staging
    verification, with a row whose bank is absent from the banks dimension. Application
    validation cannot reject it (it never saw it) and staging has no FK, so the failure
    lands on the transactions FK during the promotion INSERT -- after the DELETE.
    """
    first = _run(engine, registered_source(_rows(30), name="first.csv"))
    fingerprint_before = first.canonical_fingerprint

    def corrupt_staging_instead_of_verifying(connection, expected_rows):
        connection.execute(
            text(
                """
                INSERT INTO transactions_staging (
                    transaction_id, timestamp, amount, status, payment_method,
                    merchant_category, region, device, network,
                    sender_bank, receiver_bank, fraud_flag, source_dataset, source_row_index
                ) VALUES (
                    'TXN-ORPHAN', '2024-06-15 12:00:00+05:30', 100.00, 'SUCCESS', 'P2M',
                    'Grocery', 'Delhi', 'Android', '4G',
                    'Barclays', 'HDFC', false, 'upi_transactions_2024', 999999
                )
                """
            )
        )

    monkeypatch.setattr(
        pipeline_module, "_verify_staging", corrupt_staging_instead_of_verifying
    )

    with pytest.raises(Exception):
        _run(engine, registered_source(_rows(20), name="second.csv", dataset_name="test_fixture_dataset_second"))

    # The pre-run canonical state must be byte-identical, not merely the same row count.
    assert _count(engine, "transactions") == 30
    from aventum_ingest.verify import compute_canonical_fingerprint
    assert compute_canonical_fingerprint(engine) == fingerprint_before


def test_staging_is_empty_after_a_successful_run(engine, registered_source):
    _run(engine, registered_source(_rows(25)))
    assert _count(engine, "transactions_staging") == 0


def test_staging_never_leaks_rows_into_a_failed_run(engine, registered_source, monkeypatch):
    monkeypatch.setattr(
        pipeline_module, "_verify_staging",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail")),
    )
    with pytest.raises(RuntimeError):
        _run(engine, registered_source(_rows(15)))
    assert _count(engine, "transactions_staging") == 0
