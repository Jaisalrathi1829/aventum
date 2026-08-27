"""
Provenance regression suite for P1-1 (docs/DAY2A_ARCHITECTURE_REVIEW.md).

The defect: `source_dataset` was a hard-coded constant, so ANY file routed through
`--source` was labelled `upi_transactions_2024`, silently deleting the genuine canonical
rows and replacing them under a false provenance tag.

These tests are written to FAIL LOUDLY if a hard-coded dataset constant is ever
reintroduced, and to pin the trust boundary:

    content identity  >  filename identity
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from aventum_ingest.constants import SCHEMA_VERSION
from aventum_ingest.dataset_registry import (
    DatasetRegistrationError,
    UnknownDatasetError,
    find_by_sha256,
    register_dataset,
)
from aventum_ingest.integrity import fingerprint_source
from aventum_ingest.pipeline import RunStatus, run_ingestion
from aventum_ingest.verify import compute_canonical_fingerprint
from tests.conftest import CANONICAL_DATASET_NAME, TEST_DATASET, make_row

# The bytes Day 1 audited and the Day 2A review re-verified.
AUDITED_SHA256 = "8e46a45fd12c3e9e75a7cf1ac73604bdd9b2bd72859e3374d0153256ac4c89b6"


def _rows(count: int, start: int = 1, **overrides) -> list[dict]:
    return [make_row(start + i, **overrides) for i in range(count)]


def _count(engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def _datasets_in_transactions(engine) -> dict[str, int]:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                text("SELECT source_dataset, COUNT(*) FROM transactions GROUP BY source_dataset")
            ).all()
        )


# ==========================================================================
# Case A -- Unknown source is refused and changes nothing
# ==========================================================================

def test_case_a_unknown_source_is_rejected(engine, source_factory):
    config = source_factory(_rows(5), name="unregistered.csv")
    with pytest.raises(UnknownDatasetError):
        run_ingestion(engine, config)


def test_case_a_unknown_source_leaves_canonical_fingerprint_identical(
    engine, registered_source, source_factory
):
    """The required invariant: before-fingerprint == after-fingerprint, not just row count."""
    run_ingestion(engine, registered_source(_rows(30), name="known.csv"))
    fingerprint_before = compute_canonical_fingerprint(engine)
    rows_before = _count(engine, "transactions")

    with pytest.raises(UnknownDatasetError):
        run_ingestion(engine, source_factory(_rows(9), name="rogue.csv"))

    assert compute_canonical_fingerprint(engine) == fingerprint_before
    assert _count(engine, "transactions") == rows_before


def test_case_a_unknown_source_writes_no_false_source_dataset(
    engine, registered_source, source_factory
):
    run_ingestion(engine, registered_source(_rows(12), name="known.csv"))
    with pytest.raises(UnknownDatasetError):
        run_ingestion(engine, source_factory(_rows(4), name="rogue.csv"))

    # Only the genuinely-ingested dataset appears; nothing was labelled with a name it
    # did not earn.
    assert _datasets_in_transactions(engine) == {TEST_DATASET: 12}


def test_case_a_unknown_source_opens_no_ingestion_run(engine, source_factory):
    """
    Refusal happens before a run row is opened.

    A run row would have to carry a `source_dataset`, and there is no honest value to put
    there for unidentifiable bytes -- inventing one would be a smaller version of the very
    defect being fixed. The attempt surfaces as a non-zero CLI exit and a raised error.
    """
    runs_before = _count(engine, "ingestion_runs")
    with pytest.raises(UnknownDatasetError):
        run_ingestion(engine, source_factory(_rows(3), name="rogue.csv"))
    assert _count(engine, "ingestion_runs") == runs_before


def test_case_a_no_successful_run_is_recorded_for_an_unknown_source(engine, source_factory):
    with pytest.raises(UnknownDatasetError):
        run_ingestion(engine, source_factory(_rows(3), name="rogue.csv"))
    with engine.connect() as connection:
        succeeded = connection.execute(
            text("SELECT COUNT(*) FROM ingestion_runs WHERE status = :s"),
            {"s": RunStatus.SUCCEEDED},
        ).scalar_one()
    assert succeeded == 0


# ==========================================================================
# Case B -- Registration establishes identity WITHOUT loading anything
# ==========================================================================

def test_case_b_registration_binds_name_sha_and_schema_version(engine, source_factory, register_source):
    config = source_factory(_rows(6), name="newdata.csv")
    identity = register_source(config, dataset_name="my_new_dataset")

    expected_sha = fingerprint_source(config.source_path).sha256
    assert identity.dataset_name == "my_new_dataset"
    assert identity.source_sha256 == expected_sha
    assert identity.schema_version == SCHEMA_VERSION


def test_case_b_registration_does_not_touch_canonical_transactions(
    engine, registered_source, source_factory, register_source
):
    run_ingestion(engine, registered_source(_rows(20), name="known.csv"))
    fingerprint_before = compute_canonical_fingerprint(engine)

    register_source(source_factory(_rows(7), name="newdata.csv"), dataset_name="my_new_dataset")

    assert compute_canonical_fingerprint(engine) == fingerprint_before
    assert _count(engine, "transactions") == 20


def test_case_b_registration_is_persisted_and_auditable(engine, source_factory, register_source):
    config = source_factory(_rows(3), name="newdata.csv")
    register_source(config, dataset_name="my_new_dataset")

    sha = fingerprint_source(config.source_path).sha256
    with engine.connect() as connection:
        identity = find_by_sha256(connection, sha)
    assert identity is not None
    assert identity.registered_at is not None
    assert identity.registered_by == "pytest"
    assert identity.source_filename == "newdata.csv"   # metadata, not identity


def test_case_b_cannot_rebind_a_known_name_to_different_content(
    engine, source_factory, register_source
):
    """Rebinding a known name to new bytes is exactly the forgery this registry prevents."""
    register_source(source_factory(_rows(5), name="a.csv"), dataset_name="shared_name")
    with pytest.raises(DatasetRegistrationError):
        register_source(source_factory(_rows(9), name="b.csv"), dataset_name="shared_name")


def test_case_b_cannot_rebind_known_content_to_a_different_name(
    engine, source_factory, register_source
):
    config = source_factory(_rows(5), name="a.csv")
    register_source(config, dataset_name="first_name")
    with pytest.raises(DatasetRegistrationError):
        register_source(config, dataset_name="second_name")


def test_case_b_reregistering_identical_pair_is_idempotent(engine, source_factory, register_source):
    config = source_factory(_rows(5), name="a.csv")
    first = register_source(config, dataset_name="same_name")
    second = register_source(config, dataset_name="same_name")
    assert first.source_sha256 == second.source_sha256
    assert first.dataset_name == second.dataset_name


def test_case_b_canonical_dataset_name_cannot_be_hijacked(engine, source_factory, register_source):
    """No arbitrary file may claim the canonical dataset's name."""
    with pytest.raises(DatasetRegistrationError):
        register_source(
            source_factory(_rows(5), name="impostor.csv"),
            dataset_name=CANONICAL_DATASET_NAME,
        )


# ==========================================================================
# Case C -- Explicitly registered dataset ingests with correct provenance
# ==========================================================================

def test_case_c_registered_dataset_ingests_successfully(engine, registered_source):
    result = run_ingestion(
        engine, registered_source(_rows(25), name="mine.csv", dataset_name="explicit_dataset")
    )
    assert result.status == RunStatus.SUCCEEDED
    assert result.rows_inserted == 25


def test_case_c_ingestion_run_records_the_resolved_dataset(engine, registered_source):
    result = run_ingestion(
        engine, registered_source(_rows(15), name="mine.csv", dataset_name="explicit_dataset")
    )
    with engine.connect() as connection:
        recorded = connection.execute(
            text("SELECT source_dataset FROM ingestion_runs WHERE ingestion_run_id = :rid"),
            {"rid": result.ingestion_run_id},
        ).scalar_one()
    assert recorded == "explicit_dataset"


def test_case_c_every_promoted_row_carries_the_resolved_dataset(engine, registered_source):
    run_ingestion(
        engine, registered_source(_rows(18), name="mine.csv", dataset_name="explicit_dataset")
    )
    assert _datasets_in_transactions(engine) == {"explicit_dataset": 18}


def test_case_c_run_and_row_provenance_come_from_the_same_identity(engine, registered_source):
    """ingestion_runs.source_dataset == transactions.source_dataset for every promoted row."""
    result = run_ingestion(
        engine, registered_source(_rows(20), name="mine.csv", dataset_name="explicit_dataset")
    )
    with engine.connect() as connection:
        mismatched = connection.execute(
            text(
                """
                SELECT COUNT(*) FROM transactions t
                JOIN ingestion_runs r ON r.ingestion_run_id = t.ingestion_run_id
                WHERE t.source_dataset IS DISTINCT FROM r.source_dataset
                """
            )
        ).scalar_one()
        orphaned = connection.execute(
            text("SELECT COUNT(*) FROM transactions WHERE ingestion_run_id <> :rid"),
            {"rid": result.ingestion_run_id},
        ).scalar_one()
    assert mismatched == 0
    assert orphaned == 0


def test_case_c_row_level_lineage_reaches_the_source_hash(engine, registered_source):
    config = registered_source(_rows(10), name="mine.csv", dataset_name="explicit_dataset")
    run_ingestion(engine, config)

    expected_sha = fingerprint_source(config.source_path).sha256
    with engine.connect() as connection:
        shas = connection.execute(
            text(
                """
                SELECT DISTINCT r.source_sha256
                FROM transactions t
                JOIN ingestion_runs r ON r.ingestion_run_id = t.ingestion_run_id
                """
            )
        ).scalars().all()
    assert shas == [expected_sha]


# ==========================================================================
# Case D -- The real dataset still resolves and loads exactly as audited
# ==========================================================================

@pytest.mark.slow
def test_case_d_real_dataset_resolves_to_its_registered_identity(engine, real_source_config):
    result = run_ingestion(engine, real_source_config, force=True)

    assert result.identity is not None
    assert result.identity.dataset_name == CANONICAL_DATASET_NAME
    assert result.identity.source_sha256 == AUDITED_SHA256
    assert result.source.sha256 == AUDITED_SHA256
    assert result.status == RunStatus.SUCCEEDED
    assert result.rows_read == 250_000
    assert result.rows_valid == 250_000
    assert result.rows_rejected == 0
    assert result.rows_inserted == 250_000
    assert _datasets_in_transactions(engine) == {CANONICAL_DATASET_NAME: 250_000}
    # The fix must not change canonical CONTENT -- only how its provenance is established.
    assert result.canonical_fingerprint == (
        "12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8"
    )


# ==========================================================================
# Case E -- The old bug must never reappear
# ==========================================================================

def test_case_e_a_different_file_is_never_labelled_as_the_canonical_dataset(
    engine, source_factory
):
    """
    THE regression test for P1-1.

    Before the fix this file would have been ingested, labelled `upi_transactions_2024`,
    and would have deleted the genuine canonical rows. It must now be refused outright.
    """
    config = source_factory(_rows(7), name="pretender.csv")

    with pytest.raises(UnknownDatasetError):
        run_ingestion(engine, config)

    assert CANONICAL_DATASET_NAME not in _datasets_in_transactions(engine)


def test_case_e_registered_non_canonical_file_never_acquires_the_canonical_name(
    engine, registered_source
):
    """
    Even a properly REGISTERED file must carry its own name, never the canonical one.

    This is what fails if someone reintroduces a hard-coded `source_dataset` constant:
    the rows would come back labelled `upi_transactions_2024` despite being registered
    under a different identity.
    """
    result = run_ingestion(
        engine, registered_source(_rows(11), name="other.csv", dataset_name="some_other_dataset")
    )

    assert result.identity.dataset_name == "some_other_dataset"
    assert _datasets_in_transactions(engine) == {"some_other_dataset": 11}

    with engine.connect() as connection:
        canonical_labelled = connection.execute(
            text("SELECT COUNT(*) FROM transactions WHERE source_dataset = :ds"),
            {"ds": CANONICAL_DATASET_NAME},
        ).scalar_one()
        run_dataset = connection.execute(
            text("SELECT source_dataset FROM ingestion_runs WHERE ingestion_run_id = :rid"),
            {"rid": result.ingestion_run_id},
        ).scalar_one()

    assert canonical_labelled == 0
    assert run_dataset == "some_other_dataset"


def test_case_e_one_dataset_cannot_delete_another_datasets_rows(engine, registered_source):
    """Promotion is scoped to the resolved identity, so datasets cannot displace each other."""
    run_ingestion(engine, registered_source(_rows(10, start=1), name="a.csv", dataset_name="ds_a"))
    run_ingestion(
        engine, registered_source(_rows(6, start=900), name="b.csv", dataset_name="ds_b")
    )
    assert _datasets_in_transactions(engine) == {"ds_a": 10, "ds_b": 6}


# ==========================================================================
# Trust boundary -- content identity beats filename identity
# ==========================================================================

def test_renaming_a_file_does_not_change_its_dataset_identity(
    engine, source_factory, register_source, tmp_path
):
    """Same bytes under a new filename resolve to the SAME registered identity."""
    config = source_factory(_rows(8), name="original_name.csv")
    register_source(config, dataset_name="stable_identity")

    renamed = tmp_path / "completely_different_name.csv"
    renamed.write_bytes(config.source_path.read_bytes())

    from aventum_ingest.config import Config

    result = run_ingestion(
        engine,
        Config(database_url=config.database_url, source_path=renamed, project_root=tmp_path),
    )
    assert result.identity.dataset_name == "stable_identity"
    assert _datasets_in_transactions(engine) == {"stable_identity": 8}


def test_editing_a_file_destroys_its_dataset_identity(
    engine, source_factory, register_source, tmp_path
):
    """Same filename, changed bytes: identity must NOT survive."""
    config = source_factory(_rows(8), name="stable_name.csv")
    register_source(config, dataset_name="content_bound_identity")

    # Overwrite in place -- filename identical, content different.
    from tests.conftest import write_source_csv

    write_source_csv(config.source_path, _rows(9))

    with pytest.raises(UnknownDatasetError):
        run_ingestion(engine, config)


def test_identity_is_keyed_on_content_not_filename_in_the_registry(
    engine, source_factory, register_source
):
    """The registry's primary key is the hash; filename is stored only as metadata."""
    config = source_factory(_rows(5), name="whatever.csv")
    identity = register_source(config, dataset_name="hash_keyed")

    sha = fingerprint_source(config.source_path).sha256
    with engine.connect() as connection:
        by_hash = find_by_sha256(connection, sha)
    assert by_hash is not None
    assert by_hash.dataset_name == identity.dataset_name


# ==========================================================================
# Schema-version binding
# ==========================================================================

def test_dataset_registered_under_a_different_schema_version_is_refused(
    engine, source_factory
):
    """
    A registration is bound to the ingestion-contract version it was made under.

    If the source->canonical mapping later changes, the stored identity no longer
    describes what this code would produce, so it must be re-registered deliberately.
    """
    config = source_factory(_rows(5), name="oldcontract.csv")
    source = fingerprint_source(config.source_path)
    with engine.begin() as connection:
        register_dataset(
            connection,
            source=source,
            dataset_name="old_contract_dataset",
            schema_version="0.0.1-obsolete",
        )

    with pytest.raises(UnknownDatasetError, match="schema_version"):
        run_ingestion(engine, config)
