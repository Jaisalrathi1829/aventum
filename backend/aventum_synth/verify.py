"""
Post-generation verification, staleness assessment, and demo-readiness analysis.

Re-queries the database rather than trusting the generator's in-memory counters, for
the same reason Day 2A's verification does: a check that trusts the thing it is checking
is not a check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text

from .calibration import (
    GATEWAY_TRAFFIC_WEIGHT,
    LATENCY_REGIME_PARAMS,
    RESPONSE_TAXONOMY,
    absolute_failure_probabilities,
)

# Distribution checks are bounded by SAMPLING ERROR, not by a fixed percentage.
#
# A fixed tolerance is wrong at both ends: too loose at 250k rows (where a real drift
# would hide inside it) and meaningless at fixture scale (where a handful of failures
# per gateway swings the rate wildly). Using k binomial standard errors makes the bound
# tighten automatically as the sample grows, so the same check is rigorous in production
# and valid in tests.
#
# k = 4 is deliberately conservative: for a correct generator the per-gateway false-alarm
# probability is ~6e-5, so a failure here indicates a real problem rather than noise.
DISTRIBUTION_SIGMA_TOLERANCE = 4.0


def _binomial_standard_error(probability: float, sample_size: int) -> float:
    """Standard error of a proportion. Returns 0 for an empty sample."""
    if sample_size <= 0:
        return 0.0
    probability = min(max(probability, 0.0), 1.0)
    return (probability * (1.0 - probability) / sample_size) ** 0.5


class StalenessState:
    CURRENT = "CURRENT"
    STALE_INGESTION_MISMATCH = "STALE_INGESTION_MISMATCH"
    STALE_INCOMPLETE_COVERAGE = "STALE_INCOMPLETE_COVERAGE"
    ABSENT = "ABSENT"


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


def _jsonable(value: Any) -> Any:
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
class VerificationReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def add(self, name: str, expected: Any, actual: Any, detail: str = "") -> None:
        self.checks.append(
            Check(name=name, passed=expected == actual, expected=expected,
                  actual=actual, detail=detail)
        )

    def add_bool(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name=name, passed=passed, expected=True,
                                 actual=passed, detail=detail))

    def summary(self) -> str:
        if self.passed:
            return f"All {len(self.checks)} synthetic-infrastructure checks passed."
        lines = [f"{len(self.failures)} of {len(self.checks)} checks FAILED:"]
        for c in self.failures:
            lines.append(
                f"  - {c.name}: expected {c.expected!r}, got {c.actual!r}"
                + (f" ({c.detail})" if c.detail else "")
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "total_checks": len(self.checks),
            "failed_checks": len(self.failures),
            "checks": [c.to_dict() for c in self.checks],
        }


def _scalar(connection, sql: str, **params):
    return connection.execute(text(sql), params).scalar()


def assess_staleness(engine: Engine) -> dict:
    """
    Determine whether the synthetic population still matches the canonical load.

    Staleness policy (Day 2B §21): synthetic assignments are bound to the canonical
    ingestion run that produced the transactions they describe. If canonical data is
    re-ingested, the FK cascade removes the assignments, and any surviving generation
    run whose `source_ingestion_run_id` no longer matches is reported STALE. Stale
    populations are never silently reused -- regeneration is required.
    """
    with engine.connect() as connection:
        canonical_run = _scalar(
            connection, "SELECT MIN(ingestion_run_id) FROM transactions"
        )
        canonical_rows = _scalar(connection, "SELECT COUNT(*) FROM transactions")
        run = connection.execute(
            text(
                """
                SELECT generation_run_id, source_ingestion_run_id, rows_generated,
                       generation_fingerprint, generation_seed, generation_config_version,
                       synthetic_model_version
                FROM synthetic_generation_runs
                WHERE status = 'SUCCEEDED'
                ORDER BY generation_run_id DESC LIMIT 1
                """
            )
        ).mappings().first()

        if run is None:
            return {
                "state": StalenessState.ABSENT,
                "detail": "No SUCCEEDED synthetic generation run exists.",
                "canonical_ingestion_run_id": canonical_run,
                "generation_run_id": None,
            }

        assigned = _scalar(
            connection,
            "SELECT COUNT(*) FROM synthetic_infrastructure_assignments "
            "WHERE generation_run_id = :grid",
            grid=run["generation_run_id"],
        )

    if run["source_ingestion_run_id"] != canonical_run:
        state = StalenessState.STALE_INGESTION_MISMATCH
        detail = (
            f"Generation run {run['generation_run_id']} was built against ingestion run "
            f"{run['source_ingestion_run_id']}, but canonical transactions now belong to "
            f"ingestion run {canonical_run}. Regenerate before use."
        )
    elif assigned != canonical_rows:
        state = StalenessState.STALE_INCOMPLETE_COVERAGE
        detail = (
            f"Generation run {run['generation_run_id']} covers {assigned:,} of "
            f"{canonical_rows:,} canonical transactions. Regenerate before use."
        )
    else:
        state = StalenessState.CURRENT
        detail = (
            f"Generation run {run['generation_run_id']} matches ingestion run "
            f"{canonical_run} and covers all {canonical_rows:,} transactions."
        )

    return {
        "state": state,
        "detail": detail,
        "canonical_ingestion_run_id": canonical_run,
        "canonical_rows": canonical_rows,
        "generation_run_id": run["generation_run_id"],
        "source_ingestion_run_id": run["source_ingestion_run_id"],
        "assignments": assigned,
        "generation_fingerprint": run["generation_fingerprint"],
    }


def verify_generation(engine: Engine, generation_run_id: int) -> VerificationReport:
    """Verify coverage, provenance, coherence, and distribution bounds."""
    report = VerificationReport()
    expected_failure_probs = None

    with engine.connect() as connection:
        canonical_rows = _scalar(connection, "SELECT COUNT(*) FROM transactions")
        observed_failure_rate = float(
            _scalar(
                connection,
                "SELECT SUM((status='FAILED')::int)::float / COUNT(*) FROM transactions",
            )
        )
        expected_failure_probs = absolute_failure_probabilities(observed_failure_rate)

        # --- Coverage ---
        assigned = _scalar(
            connection,
            "SELECT COUNT(*) FROM synthetic_infrastructure_assignments "
            "WHERE generation_run_id = :g", g=generation_run_id,
        )
        report.add("assignments_cover_every_transaction", canonical_rows, assigned)

        distinct_txn = _scalar(
            connection,
            "SELECT COUNT(DISTINCT transaction_id) FROM synthetic_infrastructure_assignments "
            "WHERE generation_run_id = :g", g=generation_run_id,
        )
        report.add("one_assignment_per_transaction", canonical_rows, distinct_txn)

        orphans = _scalar(
            connection,
            """
            SELECT COUNT(*) FROM synthetic_infrastructure_assignments a
            LEFT JOIN transactions t ON t.transaction_id = a.transaction_id
            WHERE t.transaction_id IS NULL
            """,
        )
        report.add("orphaned_assignments", 0, int(orphans))

        # --- Provenance ---
        non_synthetic = _scalar(
            connection,
            "SELECT COUNT(*) FROM synthetic_infrastructure_assignments "
            "WHERE is_synthetic IS NOT TRUE",
        )
        report.add("rows_not_flagged_synthetic", 0, int(non_synthetic))

        lineage_mismatch = _scalar(
            connection,
            """
            SELECT COUNT(*) FROM synthetic_infrastructure_assignments a
            JOIN transactions t ON t.transaction_id = a.transaction_id
            WHERE a.source_ingestion_run_id <> t.ingestion_run_id
            """,
        )
        report.add("assignments_with_mismatched_ingestion_run", 0, int(lineage_mismatch))

        missing_calibration = _scalar(
            connection,
            "SELECT COUNT(*) FROM synthetic_gateways "
            "WHERE calibration_reference_name IS NULL OR length(calibration_reference_name) = 0",
        )
        report.add("gateways_missing_calibration_provenance", 0, int(missing_calibration))

        # --- Internal coherence (belt-and-braces alongside the DB CHECKs) ---
        incoherent_timeout = _scalar(
            connection,
            """
            SELECT COUNT(*) FROM synthetic_infrastructure_assignments
            WHERE (gateway_response_code = 'TIMEOUT') <> (latency_regime = 'TIMEOUT')
            """,
        )
        report.add("timeout_response_regime_mismatches", 0, int(incoherent_timeout))

        success_with_failure_response = _scalar(
            connection,
            """
            SELECT COUNT(*) FROM synthetic_infrastructure_assignments a
            JOIN transactions t ON t.transaction_id = a.transaction_id
            WHERE t.status = 'SUCCESS' AND a.gateway_response_code <> 'APPROVED'
            """,
        )
        report.add("success_rows_with_failure_response", 0, int(success_with_failure_response))

        failed_with_approved = _scalar(
            connection,
            """
            SELECT COUNT(*) FROM synthetic_infrastructure_assignments a
            JOIN transactions t ON t.transaction_id = a.transaction_id
            WHERE t.status = 'FAILED' AND a.gateway_response_code = 'APPROVED'
            """,
        )
        report.add("failed_rows_with_approved_response", 0, int(failed_with_approved))

        # Regime bands must hold: no NORMAL row may reach timeout territory.
        normal_over_cap = _scalar(
            connection,
            "SELECT COUNT(*) FROM synthetic_infrastructure_assignments "
            "WHERE latency_regime = 'NORMAL' AND gateway_latency_ms > :cap",
            cap=LATENCY_REGIME_PARAMS["NORMAL"]["cap_ms"],
        )
        report.add("normal_regime_rows_above_cap", 0, int(normal_over_cap))

        timeout_under_floor = _scalar(
            connection,
            "SELECT COUNT(*) FROM synthetic_infrastructure_assignments "
            "WHERE latency_regime = 'TIMEOUT' AND gateway_latency_ms < :floor",
            floor=LATENCY_REGIME_PARAMS["TIMEOUT"]["floor_ms"],
        )
        report.add("timeout_regime_rows_below_floor", 0, int(timeout_under_floor))

        # --- Vocabulary ---
        bad_response = set(
            connection.execute(
                text(
                    "SELECT DISTINCT gateway_response_code "
                    "FROM synthetic_infrastructure_assignments"
                )
            ).scalars()
        ) - set(RESPONSE_TAXONOMY)
        report.add("responses_outside_taxonomy", set(), bad_response)

        # --- Health (Day 2B baseline must be entirely HEALTHY) ---
        non_healthy = _scalar(
            connection,
            "SELECT COUNT(*) FROM synthetic_infrastructure_assignments "
            "WHERE gateway_health_state <> 'HEALTHY'",
        )
        report.add("baseline_rows_not_healthy", 0, int(non_healthy),
                   detail="Day 2B injects no degradation")

        # --- Distribution bounds ---
        rows = connection.execute(
            text(
                """
                SELECT a.selected_gateway_id AS gateway,
                       COUNT(*)              AS total,
                       SUM((t.status = 'FAILED')::int) AS failed
                FROM synthetic_infrastructure_assignments a
                JOIN transactions t ON t.transaction_id = a.transaction_id
                WHERE a.generation_run_id = :g
                GROUP BY a.selected_gateway_id
                """
            ),
            {"g": generation_run_id},
        ).mappings().all()

    total_assigned = sum(r["total"] for r in rows) or 1
    k = DISTRIBUTION_SIGMA_TOLERANCE

    for r in sorted(rows, key=lambda x: x["gateway"]):
        gateway = r["gateway"]

        share = r["total"] / total_assigned
        expected_share = GATEWAY_TRAFFIC_WEIGHT[gateway]
        share_tolerance = k * _binomial_standard_error(expected_share, total_assigned)
        report.add_bool(
            f"traffic_share_within_bounds[{gateway}]",
            abs(share - expected_share) <= share_tolerance,
            detail=f"share={share:.5f}, configured={expected_share:.5f}, "
                   f"|diff|={abs(share - expected_share):.5f}, tol={k:g}sigma={share_tolerance:.5f} "
                   f"(n={total_assigned:,})",
        )

        failure_rate = r["failed"] / r["total"] if r["total"] else 0.0
        expected_rate = expected_failure_probs[gateway]
        rate_tolerance = k * _binomial_standard_error(expected_rate, r["total"])
        report.add_bool(
            f"failure_rate_within_bounds[{gateway}]",
            abs(failure_rate - expected_rate) <= rate_tolerance,
            detail=f"rate={failure_rate:.5f}, calibrated={expected_rate:.5f}, "
                   f"|diff|={abs(failure_rate - expected_rate):.5f}, "
                   f"tol={k:g}sigma={rate_tolerance:.5f} (n={r['total']:,})",
        )

    return report


def cohort_volumes(engine: Engine, limit: int = 15) -> list[dict]:
    """
    Baseline volume for gateway x sender_bank x payment_method cohorts.

    Readiness analysis only -- this does not detect anything, it just reports whether
    later analysis would have enough data to work with.
    """
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT a.selected_gateway_id AS gateway,
                       t.sender_bank         AS sender_bank,
                       t.payment_method      AS payment_method,
                       COUNT(*)              AS volume,
                       SUM((t.status = 'FAILED')::int) AS failed
                FROM synthetic_infrastructure_assignments a
                JOIN transactions t ON t.transaction_id = a.transaction_id
                GROUP BY 1, 2, 3
                ORDER BY volume DESC
                LIMIT :lim
                """
            ),
            {"lim": limit},
        ).mappings().all()
    return [
        {
            "gateway": r["gateway"],
            "sender_bank": r["sender_bank"],
            "payment_method": r["payment_method"],
            "volume": r["volume"],
            "baseline_failure_rate_pct": round(100.0 * r["failed"] / r["volume"], 3),
        }
        for r in rows
    ]
