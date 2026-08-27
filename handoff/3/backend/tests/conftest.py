"""
Shared pytest fixtures.

Database tests run against a SEPARATE `aventum_test` database on the same instance, so
they can truncate freely without ever touching the real canonical load.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from aventum_ingest.config import Config, load_config
from aventum_ingest.constants import EXPECTED_SOURCE_COLUMNS, SCHEMA_VERSION
from aventum_ingest.dataset_registry import register_dataset
from aventum_ingest.db import build_engine, database_is_reachable
from aventum_ingest.integrity import fingerprint_source

TEST_DB_NAME = "aventum_test"

# Dataset name used by fixture-built sources. Deliberately NOT `upi_transactions_2024`:
# unit tests must never assert that arbitrary fixture bytes carry the canonical
# dataset's identity -- that assumption was the P1-1 defect.
TEST_DATASET = "test_fixture_dataset"

# The real dataset's registered name, seeded by migration 0002.
CANONICAL_DATASET_NAME = "upi_transactions_2024"


def _admin_url(base_url: str) -> str:
    """Point at the maintenance database so the test database can be (re)created."""
    return base_url.rsplit("/", 1)[0] + "/postgres"


def _test_url(base_url: str) -> str:
    return base_url.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"


@pytest.fixture(scope="session")
def base_database_url() -> str:
    return os.getenv("AVENTUM_DATABASE_URL", load_config().database_url)


@pytest.fixture(scope="session")
def test_database_url(base_database_url: str) -> str:
    """Create a clean `aventum_test` database and migrate it to head."""
    admin_engine = create_engine(_admin_url(base_database_url), isolation_level="AUTOCOMMIT")
    if not database_is_reachable(admin_engine):
        pytest.skip("PostgreSQL is not reachable; start it with `docker compose up -d`.")

    with admin_engine.connect() as connection:
        connection.execute(
            text(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{TEST_DB_NAME}' AND pid <> pg_backend_pid()"
            )
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        connection.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin_engine.dispose()

    url = _test_url(base_database_url)

    # Run the real migrations, so tests exercise the same DDL that production uses.
    from alembic import command
    from alembic.config import Config as AlembicConfig

    backend_dir = Path(__file__).resolve().parents[1]
    alembic_cfg = AlembicConfig(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", url)

    previous = os.environ.get("AVENTUM_DATABASE_URL")
    os.environ["AVENTUM_DATABASE_URL"] = url
    try:
        command.upgrade(alembic_cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("AVENTUM_DATABASE_URL", None)
        else:
            os.environ["AVENTUM_DATABASE_URL"] = previous

    return url


@pytest.fixture()
def engine(test_database_url: str):
    """
    Engine on the test database, with all data cleared before each test.

    `pool_timeout` is short on purpose: if a test leaks a connection (holding an idle
    transaction), the next TRUNCATE would otherwise block forever waiting for its
    ACCESS EXCLUSIVE lock. Failing fast turns a hang into a readable error.
    """
    eng = build_engine(test_database_url)
    eng.pool._timeout = 30
    with eng.begin() as connection:
        # Day 2B synthetic tables are truncated alongside the canonical ones. Listing
        # them explicitly (rather than relying on the FK cascade) also clears generation
        # runs and the seeded gateway/policy configuration, so each test starts from a
        # known empty synthetic state.
        connection.execute(
            text(
                "TRUNCATE TABLE transactions, transactions_staging, "
                "ingestion_rejects, ingestion_runs, "
                "synthetic_infrastructure_assignments, synthetic_gateway_health_states, "
                "synthetic_generation_runs, synthetic_routing_policy_gateways, "
                "synthetic_routing_policies, synthetic_gateway_profiles, "
                "synthetic_gateways, "
                # Day 3 incident tables. Listed explicitly rather than left to the FK
                # cascade so identities restart and each test sees predictable IDs.
                "incident_rca_results, incident_hypotheses, incident_evidence, "
                "incident_anomalies, incident_analysis_runs, "
                "simulated_incident_outcomes, incident_simulation_runs, "
                "incident_ground_truth, incidents RESTART IDENTITY CASCADE"
            )
        )
        # Clear per-test dataset registrations so each test starts from a known registry,
        # but KEEP the migration-seeded canonical identity -- the regression suite needs
        # `upi_transactions_2024` to resolve exactly as it does in production.
        connection.execute(
            text("DELETE FROM dataset_registry WHERE dataset_name <> :canonical"),
            {"canonical": CANONICAL_DATASET_NAME},
        )
    yield eng
    eng.dispose()


# --------------------------------------------------------------------------
# Source-file builders
# --------------------------------------------------------------------------

# One canonical, fully valid source row. Tests override individual fields from here so
# each test states only what it is actually varying.
VALID_ROW: dict[str, str] = {
    "transaction id": "TXN0000000001",
    "timestamp": "2024-06-15 12:30:45",
    "transaction type": "P2M",
    "merchant_category": "Grocery",
    "amount (INR)": "1500",
    "transaction_status": "SUCCESS",
    "sender_age_group": "26-35",
    "receiver_age_group": "36-45",
    "sender_state": "Maharashtra",
    "sender_bank": "SBI",
    "receiver_bank": "HDFC",
    "device_type": "Android",
    "network_type": "4G",
    "fraud_flag": "0",
    "hour_of_day": "12",
    "day_of_week": "Saturday",
    "is_weekend": "1",
}


def make_row(index: int = 1, **overrides: str) -> dict[str, str]:
    row = dict(VALID_ROW)
    row["transaction id"] = f"TXN{index:010d}"
    row.update(overrides)
    return row


def write_source_csv(
    path: Path,
    rows: list[dict[str, str]],
    columns: tuple[str, ...] = EXPECTED_SOURCE_COLUMNS,
) -> Path:
    """Write a source CSV with an explicit column list (so drift can be simulated)."""
    import csv

    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
    return path


@pytest.fixture()
def source_factory(tmp_path: Path):
    """
    Build a temporary source CSV and its matching Config, WITHOUT registering it.

    Use this for tests that need an UNREGISTERED source (unknown-dataset rejection,
    source-integrity failures, schema-drift failures). For ordinary ingestion tests use
    `registered_source`, which also binds a dataset identity.
    """

    def _factory(
        rows: list[dict[str, str]],
        columns: tuple[str, ...] = EXPECTED_SOURCE_COLUMNS,
        name: str = "source.csv",
    ) -> Config:
        csv_path = write_source_csv(tmp_path / name, rows, columns)
        return Config(
            database_url="unused-by-these-tests",
            source_path=csv_path,
            project_root=tmp_path,
        )

    return _factory


@pytest.fixture()
def register_source(engine):
    """Register a Config's file under a dataset name, returning the bound identity."""

    def _register(config: Config, dataset_name: str = TEST_DATASET):
        source = fingerprint_source(config.source_path, display_path=config.source_display_path)
        with engine.begin() as connection:
            return register_dataset(
                connection,
                source=source,
                dataset_name=dataset_name,
                schema_version=SCHEMA_VERSION,
                registered_by="pytest",
            )

    return _register


@pytest.fixture()
def registered_source(source_factory, register_source):
    """
    Build a temporary source CSV AND register its dataset identity.

    This is the normal path for ingestion tests: after the P1-1 fix an unregistered file
    is refused, so a test that wants a successful ingestion must establish identity first
    -- exactly as an operator would.
    """

    def _factory(
        rows: list[dict[str, str]],
        columns: tuple[str, ...] = EXPECTED_SOURCE_COLUMNS,
        name: str = "source.csv",
        dataset_name: str = TEST_DATASET,
    ) -> Config:
        config = source_factory(rows, columns=columns, name=name)
        register_source(config, dataset_name=dataset_name)
        return config

    return _factory


@pytest.fixture(scope="session")
def real_source_config() -> Config:
    """Config pointing at the actual 250K source, for the regression test."""
    config = load_config()
    if not config.source_path.exists():
        pytest.skip(f"Real source not found at {config.source_path}")
    return config
