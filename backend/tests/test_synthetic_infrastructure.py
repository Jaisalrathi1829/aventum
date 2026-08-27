"""
Day 2B tests: synthetic infrastructure baseline.

Covers database integrity, determinism, provenance, internal consistency, distribution
bounds, coverage, staleness, and flagship-cohort readiness.

Most tests run against a small ingested fixture population rather than the real 250K
source, so the suite stays fast; the full-scale checks are marked `slow`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from aventum_ingest.pipeline import run_ingestion
from aventum_synth import GENERATION_CONFIG_VERSION, SYNTHETIC_MODEL_VERSION
from aventum_synth.calibration import (
    FAILURE_RESPONSES,
    GATEWAY_TRAFFIC_WEIGHT,
    LATENCY_REGIME_PARAMS,
    RESPONSE_APPROVED,
    RESPONSE_TAXONOMY,
    absolute_failure_probabilities,
    derive_relative_failure_multipliers,
)
from aventum_synth.generator import (
    DEFAULT_GENERATION_SEED,
    GenerationError,
    RunStatus,
    compute_generation_fingerprint,
    run_generation,
)
from aventum_synth.outcome_model import GatewayRuntimeProfile, generate_signals
from aventum_synth.rng import digest_for, lane_uniform, lognormal_from_uniform
from aventum_synth.routing import ROUTING_POLICY_VERSION, SELECTION_METHOD, select_gateway
from aventum_synth.verify import StalenessState, assess_staleness, cohort_volumes, verify_generation
from tests.conftest import make_row


def _rows(count: int, start: int = 1, **overrides) -> list[dict]:
    return [make_row(start + i, **overrides) for i in range(count)]


def _scalar(engine, sql: str, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar()


def _canonical_rows(count: int = 400) -> list[dict]:
    """
    Fixture population with ~5% failures and timestamps spread across the audited range.

    Varied timestamps matter: a single-instant population would not exercise the health
    window logic or any time-scoped query the way real data does.
    """
    rows = []
    for i in range(count):
        status = "FAILED" if i % 20 == 0 else "SUCCESS"
        day = 1 + (i % 300)
        month = 1 + (day - 1) // 28
        day_of_month = 1 + (day - 1) % 28
        # Hours 1-22 keep every row inside the audited source range, whose lower bound
        # is 00:05:10 -- an hour of 00 with a low minute would be rejected by ingestion.
        hour = 1 + (i % 22)
        timestamp = f"2024-{month:02d}-{day_of_month:02d} {hour:02d}:{(i % 60):02d}:10"
        rows.append(make_row(i + 1, transaction_status=status, timestamp=timestamp))
    return rows


@pytest.fixture()
def canonical(engine, registered_source):
    """A small ingested canonical population with a realistic success/failure mix."""
    run_ingestion(engine, registered_source(_canonical_rows(), name="canonical.csv"))
    return engine


@pytest.fixture()
def generated(canonical):
    """A completed synthetic generation over the fixture population."""
    result = run_generation(canonical, generation_seed="test-seed-a")
    assert result.succeeded
    return canonical, result


# ==========================================================================
# Database integrity
# ==========================================================================

def test_generation_creates_assignment_for_every_transaction(generated):
    engine, result = generated
    assert result.rows_generated == _scalar(engine, "SELECT COUNT(*) FROM transactions")


def test_assignment_transaction_fk_rejects_unknown_transaction(generated):
    engine, result = generated
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO synthetic_infrastructure_assignments (
                        transaction_id, source_ingestion_run_id, generation_run_id,
                        routing_policy_version, eligible_gateways, selected_gateway_id,
                        selection_method, selection_seed, gateway_profile_version,
                        gateway_health_state, latency_regime, gateway_latency_ms,
                        gateway_response_code, response_attribution,
                        modeled_failure_probability
                    ) VALUES (
                        'TXN-DOES-NOT-EXIST', 1, :g, :p, '[]'::jsonb, 'gateway_A',
                        'x', 'y', 'baseline-v1', 'HEALTHY', 'NORMAL', 100.0,
                        'APPROVED', 'approved', 0.05
                    )
                    """
                ),
                {"g": result.generation_run_id, "p": ROUTING_POLICY_VERSION},
            )


def test_assignment_generation_run_fk_rejects_unknown_run(generated):
    engine, _ = generated
    txn = _scalar(engine, "SELECT transaction_id FROM transactions LIMIT 1")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO synthetic_infrastructure_assignments (
                        transaction_id, source_ingestion_run_id, generation_run_id,
                        routing_policy_version, eligible_gateways, selected_gateway_id,
                        selection_method, selection_seed, gateway_profile_version,
                        gateway_health_state, latency_regime, gateway_latency_ms,
                        gateway_response_code, response_attribution,
                        modeled_failure_probability
                    ) VALUES (
                        :t, 1, 999999, :p, '[]'::jsonb, 'gateway_A',
                        'x', 'y', 'baseline-v1', 'HEALTHY', 'NORMAL', 100.0,
                        'APPROVED', 'approved', 0.05
                    )
                    """
                ),
                {"t": txn, "p": ROUTING_POLICY_VERSION},
            )


def test_assignment_gateway_fk_rejects_unknown_gateway(generated):
    engine, result = generated
    txn = _scalar(engine, "SELECT transaction_id FROM transactions LIMIT 1")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO synthetic_infrastructure_assignments (
                        transaction_id, source_ingestion_run_id, generation_run_id,
                        routing_policy_version, eligible_gateways, selected_gateway_id,
                        selection_method, selection_seed, gateway_profile_version,
                        gateway_health_state, latency_regime, gateway_latency_ms,
                        gateway_response_code, response_attribution,
                        modeled_failure_probability
                    ) VALUES (
                        :t, 1, :g, :p, '[]'::jsonb, 'gateway_ZZZ',
                        'x', 'y', 'baseline-v1', 'HEALTHY', 'NORMAL', 100.0,
                        'APPROVED', 'approved', 0.05
                    )
                    """
                ),
                {"t": txn, "g": result.generation_run_id, "p": ROUTING_POLICY_VERSION},
            )


def test_one_assignment_per_transaction_per_run_is_enforced(generated):
    engine, result = generated
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO synthetic_infrastructure_assignments (
                        transaction_id, source_ingestion_run_id, generation_run_id,
                        routing_policy_version, eligible_gateways, selected_gateway_id,
                        selection_method, selection_seed, gateway_profile_version,
                        gateway_health_state, latency_regime, gateway_latency_ms,
                        gateway_response_code, response_attribution,
                        modeled_failure_probability
                    )
                    SELECT transaction_id, source_ingestion_run_id, generation_run_id,
                           routing_policy_version, eligible_gateways, selected_gateway_id,
                           selection_method, selection_seed, gateway_profile_version,
                           gateway_health_state, latency_regime, gateway_latency_ms,
                           gateway_response_code, response_attribution,
                           modeled_failure_probability
                    FROM synthetic_infrastructure_assignments LIMIT 1
                    """
                )
            )


@pytest.mark.parametrize("table", [
    "synthetic_infrastructure_assignments",
    "synthetic_gateways",
    "synthetic_gateway_profiles",
    "synthetic_routing_policies",
    "synthetic_gateway_health_states",
    "synthetic_generation_runs",
])
def test_is_synthetic_false_is_rejected_by_the_database(generated, table):
    """The synthetic flag is machine-enforced, not merely defaulted."""
    engine, _ = generated
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text(f"UPDATE {table} SET is_synthetic = false"))


def test_canonical_transactions_are_never_modified(generated):
    """Day 2B must not write to Day 2A's table."""
    engine, _ = generated
    assert _scalar(engine, "SELECT COUNT(*) FROM transactions") == 400
    # No synthetic column leaked onto the canonical table.
    columns = set(
        _scalar(
            engine,
            "SELECT string_agg(column_name, ',') FROM information_schema.columns "
            "WHERE table_name = 'transactions'",
        ).split(",")
    )
    for forbidden in ("gateway_id", "latency_ms", "gateway_response_code", "is_synthetic"):
        assert forbidden not in columns


# ==========================================================================
# Determinism
# ==========================================================================

def test_same_inputs_produce_identical_digest():
    a = digest_for("TXN0000000001", 1, GENERATION_CONFIG_VERSION, "seed-x")
    b = digest_for("TXN0000000001", 1, GENERATION_CONFIG_VERSION, "seed-x")
    assert a == b


@pytest.mark.parametrize("changed", [
    {"transaction_id": "TXN0000000002"},
    {"source_ingestion_run_id": 2},
    {"generation_config_version": "9.9.9"},
    {"generation_seed": "different-seed"},
])
def test_changing_any_input_changes_the_digest(changed):
    base = dict(
        transaction_id="TXN0000000001",
        source_ingestion_run_id=1,
        generation_config_version=GENERATION_CONFIG_VERSION,
        generation_seed="seed-x",
    )
    assert digest_for(**base) != digest_for(**{**base, **changed})


def test_lanes_are_independent():
    """Different lanes of one digest must not be trivially correlated."""
    digest = digest_for("TXN0000000001", 1, GENERATION_CONFIG_VERSION, "seed-x")
    values = [lane_uniform(digest, lane) for lane in range(4)]
    assert len(set(values)) == 4


def test_lognormal_draw_is_deterministic_and_bounded():
    params = LATENCY_REGIME_PARAMS["NORMAL"]
    a = lognormal_from_uniform(0.73, params["median_ms"], params["sigma"],
                               params["floor_ms"], params["cap_ms"])
    b = lognormal_from_uniform(0.73, params["median_ms"], params["sigma"],
                               params["floor_ms"], params["cap_ms"])
    assert a == b
    assert params["floor_ms"] <= a <= params["cap_ms"]


def test_extreme_uniforms_stay_inside_the_regime_band():
    """Even at the tails a NORMAL draw can never reach timeout territory."""
    params = LATENCY_REGIME_PARAMS["NORMAL"]
    for uniform in (0.0, 1e-9, 0.5, 1 - 1e-9, 1.0):
        value = lognormal_from_uniform(uniform, params["median_ms"], params["sigma"],
                                       params["floor_ms"], params["cap_ms"])
        assert params["floor_ms"] <= value <= params["cap_ms"]
        assert value < LATENCY_REGIME_PARAMS["TIMEOUT"]["floor_ms"]


def test_regeneration_with_same_seed_reproduces_the_fingerprint(canonical):
    first = run_generation(canonical, generation_seed="stable-seed")
    second = run_generation(canonical, generation_seed="stable-seed")
    assert first.generation_fingerprint == second.generation_fingerprint
    assert first.generation_run_id != second.generation_run_id


def test_changing_the_seed_changes_the_fingerprint(canonical):
    first = run_generation(canonical, generation_seed="seed-one")
    second = run_generation(canonical, generation_seed="seed-two")
    assert first.generation_fingerprint != second.generation_fingerprint


def test_gateway_assignment_is_stable_for_the_same_transaction(canonical):
    def gateway_map() -> dict[str, str]:
        # Context-managed: a leaked connection would hold an idle transaction and
        # deadlock the next test's TRUNCATE.
        with canonical.connect() as connection:
            return dict(
                connection.execute(
                    text(
                        "SELECT transaction_id, selected_gateway_id "
                        "FROM synthetic_infrastructure_assignments ORDER BY transaction_id"
                    )
                ).all()
            )

    first = run_generation(canonical, generation_seed="stable-seed")
    before = gateway_map()
    second = run_generation(canonical, generation_seed="stable-seed")

    assert before == gateway_map()
    assert first.generation_fingerprint == second.generation_fingerprint


def test_latency_and_response_are_stable_across_regeneration(canonical):
    def snapshot():
        with canonical.connect() as connection:
            return connection.execute(
                text(
                    "SELECT transaction_id, gateway_latency_ms, gateway_response_code, "
                    "latency_regime FROM synthetic_infrastructure_assignments "
                    "ORDER BY transaction_id"
                )
            ).all()

    run_generation(canonical, generation_seed="stable-seed")
    before = snapshot()
    run_generation(canonical, generation_seed="stable-seed")
    assert snapshot() == before


# ==========================================================================
# Provenance
# ==========================================================================

def test_every_assignment_is_flagged_synthetic(generated):
    engine, _ = generated
    assert _scalar(
        engine,
        "SELECT COUNT(*) FROM synthetic_infrastructure_assignments WHERE is_synthetic IS NOT TRUE",
    ) == 0


def test_assignment_lineage_matches_the_canonical_ingestion_run(generated):
    engine, _ = generated
    assert _scalar(
        engine,
        """
        SELECT COUNT(*) FROM synthetic_infrastructure_assignments a
        JOIN transactions t ON t.transaction_id = a.transaction_id
        WHERE a.source_ingestion_run_id <> t.ingestion_run_id
        """,
    ) == 0


def test_generation_run_records_full_reproducibility_metadata(generated):
    engine, result = generated
    with engine.connect() as connection:
        run = connection.execute(
            text("SELECT * FROM synthetic_generation_runs WHERE generation_run_id = :g"),
            {"g": result.generation_run_id},
        ).mappings().one()

    assert run["generation_seed"] == "test-seed-a"
    assert run["generation_config_version"] == GENERATION_CONFIG_VERSION
    assert run["synthetic_model_version"] == SYNTHETIC_MODEL_VERSION
    assert run["routing_policy_version"] == ROUTING_POLICY_VERSION
    assert run["calibration_reference_name"]
    assert run["calibration_reference_version"]
    assert run["generation_fingerprint"]
    assert run["source_ingestion_run_id"] is not None
    assert run["status"] == RunStatus.SUCCEEDED
    assert run["model_parameters"]["failure_spread_damping"]
    assert run["model_parameters"]["routing_eligibility_snapshot"]


def test_gateways_record_calibration_provenance(generated):
    engine, _ = generated
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT gateway_id, calibration_source_rail, calibration_reference_name "
                "FROM synthetic_gateways ORDER BY gateway_id"
            )
        ).mappings().all()
    assert len(rows) == 5
    for row in rows:
        assert row["calibration_reference_name"] == "nigerian_card_payment_routing"
        assert row["calibration_source_rail"].startswith("rail_")


def test_routing_decision_context_is_recorded(generated):
    engine, _ = generated
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT routing_policy_version, eligible_gateways, selected_gateway_id, "
                "selection_method, selection_seed FROM synthetic_infrastructure_assignments "
                "LIMIT 1"
            )
        ).mappings().one()
    assert row["routing_policy_version"] == ROUTING_POLICY_VERSION
    assert row["selection_method"] == SELECTION_METHOD
    assert "synthetic" in row["selection_method"]
    assert row["selected_gateway_id"] in row["eligible_gateways"]
    assert row["selection_seed"]


def test_calibration_reference_rows_are_never_imported(generated):
    """No calibration dataset row may exist anywhere in the database."""
    engine, _ = generated
    with engine.connect() as connection:
        tables = set(
            connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
    for forbidden in ("rails", "nigerian_card_payments", "calibration_transactions"):
        assert forbidden not in tables
    # The reference is present only as a NAME on synthetic config rows.
    assert _scalar(
        engine, "SELECT COUNT(DISTINCT calibration_reference_name) FROM synthetic_gateways"
    ) == 1


# ==========================================================================
# Staleness
# ==========================================================================

def test_staleness_is_current_after_generation(generated):
    engine, _ = generated
    assert assess_staleness(engine)["state"] == StalenessState.CURRENT


def test_staleness_is_absent_before_any_generation(canonical):
    assert assess_staleness(canonical)["state"] == StalenessState.ABSENT


def test_reingestion_cascades_away_stale_assignments(canonical, registered_source):
    """
    Day 2A re-ingestion deletes and re-inserts canonical rows. The FK cascade must wipe
    the synthetic population rather than leave it silently mismatched.
    """
    run_generation(canonical, generation_seed="stable-seed")
    assert _scalar(canonical, "SELECT COUNT(*) FROM synthetic_infrastructure_assignments") == 400

    run_ingestion(
        canonical, registered_source(_canonical_rows(), name="canonical.csv"), force=True
    )

    assert _scalar(canonical, "SELECT COUNT(*) FROM synthetic_infrastructure_assignments") == 0
    state = assess_staleness(canonical)["state"]
    assert state in (
        StalenessState.STALE_INGESTION_MISMATCH,
        StalenessState.STALE_INCOMPLETE_COVERAGE,
    )


def test_prior_generation_is_superseded_not_accumulated(canonical):
    run_generation(canonical, generation_seed="seed-one")
    run_generation(canonical, generation_seed="seed-two")

    # Exactly one live population; the earlier run is retained for audit but retired.
    assert _scalar(canonical, "SELECT COUNT(*) FROM synthetic_infrastructure_assignments") == 400
    assert _scalar(
        canonical,
        "SELECT COUNT(*) FROM synthetic_generation_runs WHERE status = 'SUCCEEDED'",
    ) == 1
    assert _scalar(
        canonical,
        "SELECT COUNT(*) FROM synthetic_generation_runs WHERE status = 'SUPERSEDED'",
    ) == 1


def test_generation_without_canonical_data_fails_clearly(engine):
    with pytest.raises(GenerationError, match="No canonical transactions"):
        run_generation(engine)


# ==========================================================================
# Internal consistency -- impossible combinations
# ==========================================================================

def test_success_never_carries_a_failure_response(generated):
    engine, _ = generated
    assert _scalar(
        engine,
        """
        SELECT COUNT(*) FROM synthetic_infrastructure_assignments a
        JOIN transactions t ON t.transaction_id = a.transaction_id
        WHERE t.status = 'SUCCESS' AND a.gateway_response_code <> 'APPROVED'
        """,
    ) == 0


def test_failure_never_carries_an_approved_response(generated):
    engine, _ = generated
    assert _scalar(
        engine,
        """
        SELECT COUNT(*) FROM synthetic_infrastructure_assignments a
        JOIN transactions t ON t.transaction_id = a.transaction_id
        WHERE t.status = 'FAILED' AND a.gateway_response_code = 'APPROVED'
        """,
    ) == 0


def test_timeout_response_and_timeout_regime_are_equivalent(generated):
    engine, _ = generated
    assert _scalar(
        engine,
        "SELECT COUNT(*) FROM synthetic_infrastructure_assignments "
        "WHERE (gateway_response_code = 'TIMEOUT') <> (latency_regime = 'TIMEOUT')",
    ) == 0


def test_database_rejects_success_with_timeout_combination(generated):
    """The 'SUCCESS + TIMEOUT' case Day 2B §17 names explicitly."""
    engine, _ = generated
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE synthetic_infrastructure_assignments "
                    "SET gateway_response_code = 'TIMEOUT' "
                    "WHERE gateway_response_code = 'APPROVED' AND latency_regime = 'NORMAL'"
                )
            )


def test_database_rejects_timeout_response_in_a_normal_regime(generated):
    engine, _ = generated
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE synthetic_infrastructure_assignments "
                    "SET latency_regime = 'NORMAL' WHERE gateway_response_code = 'TIMEOUT'"
                )
            )


def test_database_rejects_approved_with_declined_attribution(generated):
    engine, _ = generated
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE synthetic_infrastructure_assignments "
                    "SET response_attribution = 'issuer_side' "
                    "WHERE gateway_response_code = 'APPROVED'"
                )
            )


def test_database_rejects_unknown_response_code(generated):
    engine, _ = generated
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE synthetic_infrastructure_assignments "
                    "SET gateway_response_code = 'NOT_A_REAL_CODE' "
                    "WHERE gateway_response_code = 'APPROVED'"
                )
            )


def test_approved_may_legitimately_be_slow(canonical):
    """
    A successful-but-slow payment must remain representable.

    Forbidding it would make latency a perfect predictor of outcome, which is both
    unrealistic and would hand a later RCA component a trivial shortcut.
    """
    run_generation(canonical, generation_seed="stable-seed")
    elevated_approved = _scalar(
        canonical,
        "SELECT COUNT(*) FROM synthetic_infrastructure_assignments "
        "WHERE gateway_response_code = 'APPROVED' AND latency_regime = 'ELEVATED'",
    )
    assert elevated_approved > 0


# ==========================================================================
# Outcome-model unit behaviour
# ==========================================================================

def _profile(**overrides) -> GatewayRuntimeProfile:
    base = dict(
        gateway_id="gateway_A",
        profile_version="baseline-v1",
        baseline_failure_probability=0.05,
        latency_multiplier=1.0,
        failure_response_mix={
            "INSUFFICIENT_FUNDS": 0.26, "ISSUER_DECLINED": 0.26,
            "DO_NOT_HONOR": 0.25, "PROCESSING_ERROR": 0.21, "TIMEOUT": 0.02,
        },
        health_state="HEALTHY",
        failure_multiplier=1.0,
        health_latency_multiplier=1.0,
        timeout_multiplier=1.0,
    )
    base.update(overrides)
    return GatewayRuntimeProfile(**base)


def test_success_always_generates_approved_signals():
    digest = digest_for("TXN0000000001", 1, GENERATION_CONFIG_VERSION, "s")
    signals = generate_signals(digest, "SUCCESS", _profile())
    assert signals["gateway_response_code"] == RESPONSE_APPROVED
    assert signals["response_attribution"] == "approved"
    assert signals["latency_regime"] in ("NORMAL", "ELEVATED")


def test_failure_generates_a_declined_response():
    digest = digest_for("TXN0000000009", 1, GENERATION_CONFIG_VERSION, "s")
    signals = generate_signals(digest, "FAILED", _profile())
    assert signals["gateway_response_code"] in FAILURE_RESPONSES
    assert signals["response_attribution"] in ("issuer_side", "infrastructure_side")


def test_health_degradation_raises_modeled_failure_probability():
    """
    The Day 2C hook: raising the health multiplier must move the modeled probability
    without any other code changing.
    """
    healthy = _profile()
    degraded = _profile(health_state="DEGRADED", failure_multiplier=4.0)
    assert degraded.effective_failure_probability > healthy.effective_failure_probability
    assert degraded.effective_failure_probability == pytest.approx(0.20)


def test_health_degradation_shifts_the_response_mix_toward_infrastructure():
    """A degraded gateway should fail DIFFERENTLY, not merely more often."""
    healthy = _profile()
    degraded = _profile(health_state="DEGRADED", timeout_multiplier=5.0)
    assert (
        degraded.effective_response_mix()["TIMEOUT"]
        > healthy.effective_response_mix()["TIMEOUT"]
    )
    assert sum(degraded.effective_response_mix().values()) == pytest.approx(1.0, abs=1e-6)


def test_status_conditioned_selection_favours_worse_gateways_for_failures():
    """
    The mechanism that gives gateways differentiated observed failure rates.

    Across many transactions, FAILED outcomes must land disproportionately on gateways
    with a higher calibrated failure probability.
    """
    from aventum_synth.routing import build_candidates

    probs = absolute_failure_probabilities(0.05)
    policy_rows = [
        {"gateway_id": g, "traffic_weight": GATEWAY_TRAFFIC_WEIGHT[g],
         "eligibility_conditions": None, "is_eligible": True}
        for g in sorted(GATEWAY_TRAFFIC_WEIGHT)
    ]
    candidates = build_candidates(policy_rows, probs)

    failed_counts: dict[str, int] = {}
    success_counts: dict[str, int] = {}
    for i in range(4000):
        digest = digest_for(f"TXN{i:010d}", 1, GENERATION_CONFIG_VERSION, "s")
        failed_counts[select_gateway(digest, "FAILED", candidates)] = (
            failed_counts.get(select_gateway(digest, "FAILED", candidates), 0) + 1
        )
        success_counts[select_gateway(digest, "SUCCESS", candidates)] = (
            success_counts.get(select_gateway(digest, "SUCCESS", candidates), 0) + 1
        )

    # gateway_C has the highest calibrated failure probability, gateway_A the lowest.
    worst_share_of_failures = failed_counts.get("gateway_C", 0) / sum(failed_counts.values())
    worst_share_of_successes = success_counts.get("gateway_C", 0) / sum(success_counts.values())
    assert worst_share_of_failures > worst_share_of_successes


# ==========================================================================
# Distribution bounds
# ==========================================================================

def test_verification_passes_on_a_fresh_generation(generated):
    engine, result = generated
    report = verify_generation(engine, result.generation_run_id)
    assert report.passed, report.summary()


def test_all_five_gateways_receive_traffic(generated):
    engine, _ = generated
    with engine.connect() as connection:
        gateways = set(
            connection.execute(
                text(
                    "SELECT DISTINCT selected_gateway_id FROM synthetic_infrastructure_assignments"
                )
            ).scalars()
        )
    assert gateways == set(GATEWAY_TRAFFIC_WEIGHT)


def test_responses_stay_inside_the_taxonomy(generated):
    engine, _ = generated
    with engine.connect() as connection:
        codes = set(
            connection.execute(
                text(
                    "SELECT DISTINCT gateway_response_code "
                    "FROM synthetic_infrastructure_assignments"
                )
            ).scalars()
        )
    assert codes <= set(RESPONSE_TAXONOMY)


def test_latency_values_respect_their_regime_bands(generated):
    engine, _ = generated
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT latency_regime, MIN(gateway_latency_ms) AS lo, "
                "MAX(gateway_latency_ms) AS hi FROM synthetic_infrastructure_assignments "
                "GROUP BY latency_regime"
            )
        ).mappings().all()
    for row in rows:
        params = LATENCY_REGIME_PARAMS[row["latency_regime"]]
        assert float(row["lo"]) >= params["floor_ms"]
        assert float(row["hi"]) <= params["cap_ms"]


def test_baseline_health_is_entirely_healthy(generated):
    """Day 2B must not inject degradation."""
    engine, _ = generated
    with engine.connect() as connection:
        states = set(
            connection.execute(
                text(
                    "SELECT DISTINCT gateway_health_state "
                    "FROM synthetic_infrastructure_assignments"
                )
            ).scalars()
        )
    assert states == {"HEALTHY"}


def test_baseline_health_windows_exist_for_every_gateway(generated):
    engine, result = generated
    assert _scalar(
        engine,
        "SELECT COUNT(*) FROM synthetic_gateway_health_states WHERE generation_run_id = :g",
        g=result.generation_run_id,
    ) == 5


def test_calibrated_failure_spread_is_differentiated_but_not_an_incident():
    """
    Baseline gateways must differ, but no gateway may look like an outage.

    The damped transfer should keep the worst/best ratio well under 2x.
    """
    multipliers = derive_relative_failure_multipliers()
    ratio = max(multipliers.values()) / min(multipliers.values())
    assert 1.2 < ratio < 1.8, f"baseline spread ratio {ratio:.3f} outside documented band"


def test_absolute_failure_probabilities_preserve_the_observed_rate():
    """Attaching gateways must not distort the observed aggregate failure rate."""
    observed = 0.049504
    probs = absolute_failure_probabilities(observed)
    weighted = sum(GATEWAY_TRAFFIC_WEIGHT[g] * p for g, p in probs.items())
    assert weighted == pytest.approx(observed, rel=1e-9)


def test_observed_failure_rate_is_unchanged_by_generation(generated):
    """The synthetic layer must never alter observed marginals."""
    engine, _ = generated
    approved = _scalar(
        engine,
        "SELECT COUNT(*) FROM synthetic_infrastructure_assignments "
        "WHERE gateway_response_code = 'APPROVED'",
    )
    observed_success = _scalar(
        engine, "SELECT COUNT(*) FROM transactions WHERE status = 'SUCCESS'"
    )
    assert approved == observed_success


# ==========================================================================
# Read surface
# ==========================================================================

def test_read_surface_labels_observed_and_synthetic_distinctly(generated):
    engine, _ = generated
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT * FROM v_transaction_infrastructure LIMIT 1")
        ).mappings().one()

    assert row["transaction_provenance"] == "OBSERVED"
    assert row["infrastructure_provenance"] == "SYNTHETIC"
    assert row["infrastructure_is_synthetic"] is True
    # Column naming alone must disambiguate the two families.
    assert any(k.startswith("observed_") for k in row)
    assert any(k.startswith("synthetic_") for k in row)
    assert "observed_sender_bank" in row and "synthetic_gateway_id" in row


def test_read_surface_covers_every_assigned_transaction(generated):
    engine, _ = generated
    assert _scalar(engine, "SELECT COUNT(*) FROM v_transaction_infrastructure") == 400


# ==========================================================================
# Flagship cohort readiness
# ==========================================================================

def test_cohort_volumes_are_reported(generated):
    engine, _ = generated
    cohorts = cohort_volumes(engine, limit=5)
    assert cohorts
    for cohort in cohorts:
        assert cohort["volume"] > 0
        assert cohort["gateway"] in GATEWAY_TRAFFIC_WEIGHT
        assert 0 <= cohort["baseline_failure_rate_pct"] <= 100


# ==========================================================================
# Full-scale checks against the real 250K canonical dataset
# ==========================================================================

@pytest.mark.slow
def test_full_scale_generation_covers_every_canonical_transaction(engine, real_source_config):
    run_ingestion(engine, real_source_config, force=True)
    result = run_generation(engine, generation_seed=DEFAULT_GENERATION_SEED)

    assert result.succeeded
    assert result.rows_generated == 250_000
    report = verify_generation(engine, result.generation_run_id)
    assert report.passed, report.summary()


@pytest.mark.slow
def test_full_scale_flagship_cohort_has_usable_volume(engine, real_source_config):
    """
    A future incident cohort must be large enough for a later degradation to be
    statistically visible. Checked at gateway level, which is the resolution the
    per-day density actually supports.
    """
    run_ingestion(engine, real_source_config, force=True)
    run_generation(engine, generation_seed=DEFAULT_GENERATION_SEED)

    with engine.connect() as connection:
        smallest_gateway_volume = connection.execute(
            text(
                "SELECT MIN(n) FROM (SELECT COUNT(*) AS n FROM "
                "synthetic_infrastructure_assignments GROUP BY selected_gateway_id) s"
            )
        ).scalar_one()
    # Even the smallest gateway must carry enough traffic for daily-resolution analysis.
    assert smallest_gateway_volume > 25_000
