"""
Post-load verification and the canonical dataset fingerprint (Day 2A §13).

These checks run against the DATABASE after promotion, deliberately re-querying rather
than reusing in-memory ETL counters -- a verification that trusted the pipeline's own
bookkeeping would not be independent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text

from .constants import (
    EXPECTED_AMOUNT_MAX,
    EXPECTED_AMOUNT_MIN,
    EXPECTED_DEVICE_DISTRIBUTION,
    EXPECTED_FRAUD_FLAG_TRUE_COUNT,
    EXPECTED_NETWORK_DISTRIBUTION,
    EXPECTED_PAYMENT_METHOD_DISTRIBUTION,
    EXPECTED_ROW_COUNT,
    EXPECTED_STATUS_DISTRIBUTION,
    CANONICAL_DATASET_NAME,
    VALID_BANKS,
    VALID_REGIONS,
)


def _jsonable(value: Any) -> Any:
    """
    Render a check value as JSON-safe primitives for the audit record.

    Decimal becomes a string (never a float -- that would silently reintroduce binary
    rounding into an audited monetary bound), and sets become sorted lists so the
    serialized report is deterministic across runs.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


@dataclass
class Check:
    name: str
    passed: bool
    expected: Any
    actual: Any
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "expected": _jsonable(self.expected),
            "actual": _jsonable(self.actual),
            "detail": self.detail,
        }


@dataclass
class VerificationReport:
    checks: list[Check] = field(default_factory=list)
    canonical_fingerprint: str = ""
    dataset_invariants_asserted: bool = False
    skipped_note: str = ""

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.passed]

    def add(self, name: str, expected: Any, actual: Any, detail: str = "") -> Check:
        check = Check(name=name, passed=expected == actual, expected=expected, actual=actual,
                      detail=detail)
        self.checks.append(check)
        return check

    def add_bool(self, name: str, passed: bool, detail: str = "") -> Check:
        check = Check(name=name, passed=passed, expected=True, actual=passed, detail=detail)
        self.checks.append(check)
        return check

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "total_checks": len(self.checks),
            "failed_checks": len(self.failures),
            "canonical_fingerprint": self.canonical_fingerprint,
            "dataset_invariants_asserted": self.dataset_invariants_asserted,
            "skipped_note": self.skipped_note,
            "checks": [check.to_dict() for check in self.checks],
        }

    def summary(self) -> str:
        if self.passed:
            suffix = "" if self.dataset_invariants_asserted else f" ({self.skipped_note})"
            return f"All {len(self.checks)} post-load verification checks passed.{suffix}"
        lines = [f"{len(self.failures)} of {len(self.checks)} verification checks FAILED:"]
        for check in self.failures:
            lines.append(f"  - {check.name}: expected {check.expected!r}, got {check.actual!r}"
                         + (f" ({check.detail})" if check.detail else ""))
        return "\n".join(lines)


def compute_canonical_fingerprint(engine: Engine) -> str:
    """
    Deterministic SHA-256 over the canonical dataset content.

    Ordered by transaction_id so the value is independent of physical row order.
    NULL merchant_category is rendered as a distinct sentinel so a NULL and the literal
    string 'NULL' cannot collide. Computed server-side to avoid streaming 250k rows
    back to the client purely to hash them.
    """
    sql = text(
        """
        SELECT encode(sha256(convert_to(string_agg(row_repr, E'\\n' ORDER BY transaction_id),
                                        'UTF8')), 'hex')
        FROM (
            SELECT
                transaction_id,
                transaction_id
                || '|' || to_char(timestamp AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
                || '|' || to_char(amount, 'FM9999999999990.00')
                || '|' || status
                || '|' || payment_method
                || '|' || COALESCE(merchant_category, '\\N')
                || '|' || region
                || '|' || device
                || '|' || network
                || '|' || sender_bank
                || '|' || receiver_bank
                || '|' || CASE WHEN fraud_flag THEN '1' ELSE '0' END
                || '|' || source_dataset
                AS row_repr
            FROM transactions
        ) AS rows
        """
    )
    with engine.connect() as connection:
        return connection.execute(sql).scalar_one()


def _scalar(connection, sql: str, **params) -> Any:
    return connection.execute(text(sql), params).scalar()


def _distribution(connection, column: str) -> dict[str, int]:
    rows = connection.execute(
        text(f"SELECT {column} AS k, COUNT(*) AS n FROM transactions GROUP BY {column}")
    ).all()
    return {row.k: row.n for row in rows}


def verify_canonical_load(
    engine: Engine,
    ingestion_run_id: int | None = None,
    expect_dataset_invariants: bool = True,
    source_dataset: str = CANONICAL_DATASET_NAME,
) -> VerificationReport:
    """
    Run the Day 2A §13 verification checks against the live database.

    Two distinct families of check are asserted here, and they are deliberately kept
    separate:

      1. STRUCTURAL invariants -- true of any correct canonical load regardless of which
         source produced it (uniqueness, no duplicate rows, the P2P rule, generated
         alias consistency, provenance populated, constraints active, staging drained).
         Always asserted.

      2. DATASET invariants -- the specific counts and distributions the Day 1 audit
         measured for `upi_transactions_2024` (250,000 rows, exact status/method/device/
         network distributions, amount range, fraud count). These describe one specific
         file, so asserting them against a different source would be meaningless.
         Controlled by `expect_dataset_invariants`, which the pipeline sets by comparing
         the source SHA-256 against AUDITED_SOURCE_SHA256 -- so they are asserted exactly
         when the ingested bytes ARE the audited bytes, and explicitly recorded as
         skipped otherwise rather than silently passing.

    `source_dataset` is the RESOLVED dataset identity for the run being verified (see
    aventum_ingest/dataset_registry.py). The provenance check asserts every row carries
    that value, so a run can never leave rows labelled with a dataset it did not load.
    It defaults to the canonical dataset for standalone `cli verify` invocations.
    """
    report = VerificationReport(dataset_invariants_asserted=expect_dataset_invariants)
    if not expect_dataset_invariants:
        report.skipped_note = (
            "Day 1 dataset-specific distribution checks skipped: this source is not the "
            "audited upi_transactions_2024 file. Structural invariants were still asserted."
        )

    with engine.connect() as connection:
        # --- Row counts and identity ---
        total = _scalar(connection, "SELECT COUNT(*) FROM transactions")
        unique_ids = _scalar(connection, "SELECT COUNT(DISTINCT transaction_id) FROM transactions")

        if expect_dataset_invariants:
            report.add("total_rows", EXPECTED_ROW_COUNT, total)
            report.add("unique_transaction_ids", EXPECTED_ROW_COUNT, unique_ids)
        else:
            # Structural form of the same guarantee: every row has a distinct id.
            report.add("transaction_ids_all_distinct", total, unique_ids)

        # Full-row duplicates over the canonical business columns.
        duplicate_rows = _scalar(
            connection,
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
        report.add("duplicate_rows", 0, int(duplicate_rows))

        # --- Distributions and coverage (Day 1 audited dataset invariants) ---
        banks_present = set(
            connection.execute(
                text(
                    "SELECT DISTINCT b FROM ("
                    "  SELECT sender_bank AS b FROM transactions"
                    "  UNION SELECT receiver_bank FROM transactions"
                    ") u"
                )
            ).scalars()
        )
        regions_present = set(
            connection.execute(text("SELECT DISTINCT region FROM transactions")).scalars()
        )

        if expect_dataset_invariants:
            report.add("status_distribution", EXPECTED_STATUS_DISTRIBUTION,
                       _distribution(connection, "status"))
            report.add("payment_method_distribution", EXPECTED_PAYMENT_METHOD_DISTRIBUTION,
                       _distribution(connection, "payment_method"))
            report.add("device_distribution", EXPECTED_DEVICE_DISTRIBUTION,
                       _distribution(connection, "device"))
            report.add("network_distribution", EXPECTED_NETWORK_DISTRIBUTION,
                       _distribution(connection, "network"))

            report.add("amount_min", EXPECTED_AMOUNT_MIN,
                       _scalar(connection, "SELECT MIN(amount) FROM transactions"))
            report.add("amount_max", EXPECTED_AMOUNT_MAX,
                       _scalar(connection, "SELECT MAX(amount) FROM transactions"))

            report.add("all_expected_banks_present", set(VALID_BANKS), banks_present)
            report.add("all_expected_states_present", set(VALID_REGIONS), regions_present)
        else:
            # Structural form: whatever values are present must still be in-vocabulary.
            report.add("banks_within_expected_universe", set(), banks_present - set(VALID_BANKS))
            report.add("regions_within_expected_universe", set(),
                       regions_present - set(VALID_REGIONS))

        # --- P2P merchant-category rule ---
        p2p_with_category = _scalar(
            connection,
            "SELECT COUNT(*) FROM transactions "
            "WHERE payment_method = 'P2P' AND merchant_category IS NOT NULL",
        )
        report.add("p2p_rows_with_merchant_category", 0, int(p2p_with_category))

        non_p2p_without_category = _scalar(
            connection,
            "SELECT COUNT(*) FROM transactions "
            "WHERE payment_method <> 'P2P' AND merchant_category IS NULL",
        )
        report.add("non_p2p_rows_missing_merchant_category", 0, int(non_p2p_without_category))

        # --- Generated alias columns held (schema-deviation guarantee) ---
        alias_mismatch = _scalar(
            connection,
            "SELECT COUNT(*) FROM transactions "
            "WHERE transaction_type <> payment_method OR issuer_bank <> sender_bank",
        )
        report.add("generated_alias_mismatches", 0, int(alias_mismatch))

        # --- Retained source field (Day 1 dataset invariant) ---
        if expect_dataset_invariants:
            fraud_true = _scalar(
                connection, "SELECT COUNT(*) FROM transactions WHERE fraud_flag IS TRUE"
            )
            report.add("fraud_flag_true_count", EXPECTED_FRAUD_FLAG_TRUE_COUNT, int(fraud_true))

        # --- Provenance ---
        # Every row must carry a non-null lineage pointer, whichever dataset it belongs to.
        missing_lineage = _scalar(
            connection,
            "SELECT COUNT(*) FROM transactions "
            "WHERE ingestion_run_id IS NULL OR source_dataset IS NULL",
        )
        report.add("rows_missing_lineage", 0, int(missing_lineage))

        if ingestion_run_id is not None:
            # Both checks below are scoped by dataset/run rather than table-wide. A
            # table-wide comparison would flag another dataset's legitimately-different
            # provenance as an error -- the same "one global dataset" assumption that
            # produced P1-1. Scoped, they still catch the real failures: a row this run
            # wrote under the wrong dataset name, or a row of this dataset left behind by
            # an earlier run that promotion should have replaced.
            mislabelled = _scalar(
                connection,
                "SELECT COUNT(*) FROM transactions "
                "WHERE ingestion_run_id = :rid AND source_dataset IS DISTINCT FROM :ds",
                rid=ingestion_run_id,
                ds=source_dataset,
            )
            report.add("rows_written_by_this_run_with_wrong_dataset", 0, int(mislabelled))

            stale = _scalar(
                connection,
                "SELECT COUNT(*) FROM transactions "
                "WHERE source_dataset = :ds AND ingestion_run_id <> :rid",
                rid=ingestion_run_id,
                ds=source_dataset,
            )
            report.add("rows_of_this_dataset_not_attributed_to_this_run", 0, int(stale))

        # --- Database constraints are actually active ---
        constraint_count = _scalar(
            connection,
            """
            SELECT COUNT(*) FROM pg_constraint
            WHERE conrelid = 'transactions'::regclass AND contype IN ('c', 'p', 'f')
            """,
        )
        # 9 CHECK + 1 PK + 3 FK = 13 expected on `transactions`.
        report.add_bool(
            "database_constraints_active",
            int(constraint_count) >= 13,
            detail=f"{constraint_count} PK/FK/CHECK constraints found on transactions (expected >= 13)",
        )

        not_null_count = _scalar(
            connection,
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_name = 'transactions' AND is_nullable = 'NO'
            """,
        )
        report.add_bool(
            "not_null_constraints_active",
            int(not_null_count) >= 14,
            detail=f"{not_null_count} NOT NULL columns on transactions (expected >= 14)",
        )

        # --- Staging emptied after promotion (no leftover partial state) ---
        staging_rows = _scalar(connection, "SELECT COUNT(*) FROM transactions_staging")
        report.add("staging_rows_after_promotion", 0, int(staging_rows))

    report.canonical_fingerprint = compute_canonical_fingerprint(engine)
    return report
