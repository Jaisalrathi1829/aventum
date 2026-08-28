"""
Day 4A tests: counterfactual simulation, business impact, risk, policy, recommendation,
approval, simulated execution, idempotency, concurrency, audit, and Day 5 handoff.

FOUR PROPERTIES GET DISPROPORTIONATE ATTENTION, because everything else rests on them:

  1. PROBABILITY CONSISTENCY -- Day 4 must not own a second failure model. Tested by
     running NO_ACTION and asserting it reproduces Day 3's stored outcomes exactly, and
     by asserting the two layers compute identical probabilities from identical inputs.

  2. NUMBERS CANNOT BE INJECTED -- the recommendation builder's signature is the security
     property, so it is tested by introspection (no numeric parameter exists) AND
     adversarially (passing one raises), not merely by checking the stored values.

  3. SELECTION INDEPENDENCE -- the reroute selection must not correlate with the incident's
     own outcome draw. A regression test pins this, because the correlated version was a
     real bug that made every reroute look ~5x more effective than it is.

  4. DUPLICATE / CONCURRENT EXECUTION -- proven with real threads against the real
     database, so the guarantee demonstrably comes from the UNIQUE constraint rather than
     from test-harness timing.

The fixture population mirrors the Day 3 suite so production thresholds apply unchanged.
"""

from __future__ import annotations

import inspect
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aventum_action import SIMULATED_ADAPTER_NAME
from aventum_action.adapter import RoutingActionAdapter, SimulatedRoutingAdapter
from aventum_action.approval import (
    ApprovalError,
    build_approval_payload,
    decide_approval,
    request_approval,
)
from aventum_action.audit import ACTION_DUPLICATE_SUPPRESSED, ACTION_EXECUTED, emit
from aventum_action.execute import execute_action, rollback
from aventum_action.handoff import build_verification_handoff, provenance_chain
from aventum_action.models import Action, Approval, AuditEvent, Recommendation
from aventum_action.pipeline import (
    primary_alert_role,
    run_decision_pipeline,
    run_full_flow,
)
from aventum_action.recommendation import (
    RecommendationStateError,
    advance_status,
    build_recommendation,
    requires_approval,
)
from aventum_counterfactual import COUNTERFACTUAL_MODEL_VERSION
from aventum_counterfactual.constants import (
    ACTION_NO_ACTION,
    ACTION_REROUTE,
    CAPACITY_UNAVAILABLE,
    ELIGIBILITY_UNCONDITIONAL,
    INVALID_TARGET_NOT_HEALTHY,
    INVALID_TRAFFIC_EXCEEDS_MAX,
    MAX_TRAFFIC_PERCENTAGE,
    STATUS_INVALID,
    STATUS_VALID,
)
from aventum_counterfactual.fingerprint import (
    compute_idempotency_key,
    compute_input_fingerprint,
)
from aventum_counterfactual.impact import compute_business_impact
from aventum_counterfactual.models import CounterfactualSimulation
from aventum_counterfactual.optimize import (
    DEFAULT_NO_ACTION_MARGIN,
    build_candidate_set,
    run_candidate_sweep,
    select_best,
)
from aventum_counterfactual.risk import compute_risk
from aventum_counterfactual.simulator import (
    Candidate,
    _project_outcomes,
    _selection_rank,
    affected_cohort,
    candidate_seed,
    p_success,
    run_counterfactual,
    runtime_profile_for,
    select_rerouted,
)
from aventum_counterfactual.source import load_rca, load_world_state
from aventum_incident import INCIDENT_CONFIG_VERSION, INCIDENT_MODEL_VERSION
from aventum_incident.pipeline import run_incident_pipeline
from aventum_incident.rng import LANE_OUTCOME, incident_digest_for, lane_uniform
from aventum_incident.simulate import added_failure_probability, build_runtime_profile
from aventum_ingest.db import build_engine, build_session_factory
from aventum_ingest.pipeline import run_ingestion
from aventum_policy import POLICY_VERSION
from aventum_policy.constants import (
    ALERT_NOT_PRIMARY,
    APPROVAL_EXPIRED,
    APPROVAL_FINGERPRINT_MISMATCH,
    BENEFIT_BELOW_NO_ACTION_MARGIN,
    CONCENTRATION_EXCEEDS_BOUND,
    CONFIDENCE_BELOW_THRESHOLD,
    EVIDENCE_STRENGTH_BELOW_THRESHOLD,
    MAX_CONCENTRATION_AFTER,
    MAX_TRAFFIC_SHIFT_PERCENTAGE,
    MIN_CONFIDENCE,
    RCA_NOT_CONFIDENT,
    RECOMMENDATION_NOT_APPROVED,
    SEVERITY_BELOW_THRESHOLD,
    SIGNIFICANCE_BELOW_THRESHOLD,
    STALE_SIMULATION,
    TARGET_NOT_HEALTHY,
)
from aventum_policy.gate import validate
from aventum_synth.generator import run_generation
from tests.test_incident_intelligence import _definition, _fixture_rows

# ==========================================================================
# Fixtures — a real incident, diagnosed, ready for Day 4
# ==========================================================================


@pytest.fixture()
def day4_engine(engine, registered_source):
    run_ingestion(engine, registered_source(_fixture_rows(), name="day4-fixture.csv"))
    result = run_generation(engine, generation_seed="day4-test-seed")
    assert result.succeeded
    return engine


@pytest.fixture()
def day4_session(day4_engine) -> Session:
    with Session(day4_engine) as sess:
        yield sess


@pytest.fixture()
def run_ids4(day4_session) -> tuple[int, int]:
    row = day4_session.execute(
        text(
            "SELECT generation_run_id, source_ingestion_run_id "
            "FROM synthetic_generation_runs WHERE status = 'SUCCEEDED' "
            "ORDER BY generation_run_id DESC LIMIT 1"
        )
    ).mappings().first()
    return int(row["generation_run_id"]), int(row["source_ingestion_run_id"])


@pytest.fixture()
def diagnosed(day4_session, run_ids4):
    """A fully diagnosed golden incident — Day 4's actual input."""
    result = run_incident_pipeline(day4_session, _definition(run_ids4))
    day4_session.commit()
    return result


@pytest.fixture()
def world(day4_session, diagnosed):
    return load_world_state(day4_session, diagnosed.incident.incident_id)


@pytest.fixture()
def ids(diagnosed) -> tuple[int, int]:
    return diagnosed.incident.incident_id, diagnosed.analysis_run_id


# ==========================================================================
# Migration, schema, constraints
# ==========================================================================


def test_day4_tables_exist_with_expected_shape(day4_session):
    names = {
        r[0]
        for r in day4_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'"
            )
        ).fetchall()
    }
    for table in (
        "counterfactual_simulations", "agent_runs", "agent_tool_calls",
        "recommendations", "approvals", "actions", "audit_events",
    ):
        assert table in names, f"migration 0006 did not create {table}"


def test_migration_0006_did_not_alter_day3_tables(day4_session):
    """Day 4 is additive. A column added to a Day 3 table would be a contract breach."""
    cols = {
        r[0]
        for r in day4_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='incident_rca_results'"
            )
        ).fetchall()
    }
    # Exactly Day 3's post-P1-2 column set; nothing Day 4 bolted on.
    assert "recommendation_id" not in cols
    assert "simulation_id" not in cols
    assert {"confidence", "evidence_strength", "significance_sigma", "severity"} <= cols


def test_simulation_cannot_be_marked_not_simulated(day4_session, world, ids):
    """Provenance is DB-enforced: a projection can never be relabelled as observed."""
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    day4_session.flush()
    with pytest.raises(IntegrityError):
        day4_session.execute(
            text("UPDATE counterfactual_simulations SET is_simulated = false "
                 "WHERE simulation_id = :sid"),
            {"sid": sim.simulation_id},
        )
        day4_session.flush()
    day4_session.rollback()


def test_action_cannot_be_marked_not_simulated(day4_session, world, ids):
    """Red-team 9: the database refuses to record a Day 4 execution as real."""
    incident_id, analysis_run_id = ids
    flow = run_full_flow(
        day4_session, incident_id, analysis_run_id, approver_identity="tester"
    )
    assert flow.action is not None
    with pytest.raises(IntegrityError):
        day4_session.execute(
            text("UPDATE actions SET is_simulated = false WHERE action_id = :aid"),
            {"aid": flow.action.action_id},
        )
        day4_session.flush()
    day4_session.rollback()


def test_invalid_simulation_must_carry_a_reason(day4_session, world, ids):
    _, analysis_run_id = ids
    with pytest.raises(IntegrityError):
        day4_session.add(
            CounterfactualSimulation(
                incident_id=world.incident_id, analysis_run_id=analysis_run_id,
                candidate_key="bogus", action_type=ACTION_NO_ACTION, traffic_percentage=0,
                status=STATUS_INVALID, invalid_reason=None,  # incoherent
                simulation_seed="x", input_fingerprint="f" * 64,
                model_version="1.0.0", policy_version="1.0.0", profile_version="baseline-v1",
            )
        )
        day4_session.flush()
    day4_session.rollback()


# ==========================================================================
# Counterfactual validity and determinism
# ==========================================================================


def test_no_action_is_simulated_as_a_real_row_not_a_null(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    assert sim.status == STATUS_VALID
    assert sim.simulation_id is not None
    assert sim.affected_population > 0
    assert sim.projected_success_rate is not None
    assert sim.simulation_fingerprint is not None


def test_no_action_reproduces_day3_outcomes_exactly(day4_session, world, ids):
    """
    THE probability-consistency test.

    NO_ACTION reroutes nothing, so every projected outcome must equal the Day 3 stored
    outcome. If Day 4 had its own failure model, this is where the two would diverge.
    """
    cohort = affected_cohort(world)
    outcomes = _project_outcomes(
        world, Candidate(action_type=ACTION_NO_ACTION), cohort, rerouted=set()
    )
    assert outcomes
    for o, txn in zip(outcomes, cohort):
        assert o.projected_status == txn.current_status
        assert o.projected_latency_ms == txn.current_latency_ms
        assert o.projected_response_code == txn.current_response_code
        assert not o.rerouted


def test_no_action_projected_failure_count_matches_day3(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    day3_failures = day4_session.execute(
        text(
            "SELECT COUNT(*) FROM simulated_incident_outcomes "
            "WHERE incident_id = :iid AND in_affected_cohort = true "
            "AND simulated_status = 'FAILED'"
        ),
        {"iid": world.incident_id},
    ).scalar()
    assert sim.projected_failure_count == day3_failures


def test_day4_and_day3_compute_identical_success_probabilities(world):
    """
    There is ONE authoritative P_success. Day 4 reaches it through Day 3's own
    constructor, so identical inputs must give bit-identical values.
    """
    class _Shim:
        failure_multiplier = world.failure_multiplier
        latency_multiplier = world.latency_multiplier
        timeout_multiplier = world.timeout_multiplier

    for gateway_id, profile in world.profiles.items():
        for degraded in (True, False):
            day3 = build_runtime_profile(profile.as_profile_row(), _Shim(), degraded)
            day4 = runtime_profile_for(world, gateway_id, degraded)
            assert day4.effective_failure_probability == day3.effective_failure_probability
            assert p_success(day4) == 1.0 - day3.effective_failure_probability
            assert day4.effective_latency_multiplier == day3.effective_latency_multiplier
            assert day4.effective_response_mix() == day3.effective_response_mix()


def test_reroute_selection_is_deterministic(world):
    cohort = affected_cohort(world)
    a = select_rerouted(cohort, world, 20.0)
    b = select_rerouted(cohort, world, 20.0)
    assert a == b
    assert len(a) == int(len(cohort) * 0.20)


def test_reroute_selection_is_nested_across_percentages(world):
    """10% ⊂ 20% ⊂ 30% — raising the shift ADDS traffic rather than swapping it."""
    cohort = affected_cohort(world)
    ten = select_rerouted(cohort, world, 10.0)
    twenty = select_rerouted(cohort, world, 20.0)
    thirty = select_rerouted(cohort, world, 30.0)
    assert ten < twenty < thirty


def test_reroute_selection_never_exceeds_the_requested_share(world):
    """floor(), not round() — a 10% shift must never move more than 10%."""
    cohort = affected_cohort(world)
    for pct in (10.0, 20.0, 30.0):
        chosen = select_rerouted(cohort, world, pct)
        assert len(chosen) <= len(cohort) * pct / 100.0


def test_reroute_selection_is_independent_of_the_incident_outcome_draw(world):
    """
    REGRESSION TEST for a real bug.

    The first implementation hashed the same field tuple Day 3 uses for its outcome
    draw, so ordering by that digest ordered the cohort by exactly the quantity that
    decided failure: a 10% reroute picked 26 transactions of which 26 had failed, and
    the projected benefit was overstated roughly 5x.

    A correct selection must be uncorrelated with failure. With ~14% of the cohort
    incident-damaged, a 20% selection should contain roughly its population share of
    damaged rows -- certainly not all of them.
    """
    cohort = affected_cohort(world)
    damaged = {
        t.transaction_id
        for t in cohort
        if t.observed_status == "SUCCESS" and t.current_status == "FAILED"
    }
    assert damaged, "fixture produced no incident-induced failures"

    chosen = select_rerouted(cohort, world, 20.0)
    captured = len(damaged & chosen)
    # A perfectly correlated selector captures ALL damaged rows it has room for.
    assert captured < len(damaged), (
        "reroute selection captured every incident-induced failure — it is correlated "
        "with the outcome draw, which is the bug _SELECTION_DOMAIN exists to prevent"
    )
    # And it should sit near the population share rather than at either extreme.
    share = len(chosen) / len(cohort)
    assert captured <= len(damaged) * (share + 0.35)


def test_selection_rank_differs_from_the_day3_outcome_key(world):
    """The domain separator must actually change the digest."""
    txn = affected_cohort(world)[0]
    day3_digest = incident_digest_for(
        transaction_id=txn.transaction_id,
        incident_key=world.incident_key,
        simulation_seed=world.incident_seed,
        incident_model_version=INCIDENT_MODEL_VERSION,
        incident_config_version=INCIDENT_CONFIG_VERSION,
    ).hex()
    assert _selection_rank(txn.transaction_id, world) != day3_digest


def test_rerouted_rows_are_regenerated_and_others_reused(world):
    cohort = affected_cohort(world)
    candidate = Candidate(
        action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
        traffic_percentage=20.0, source_gateway_id=world.affected_gateway_id,
    )
    rerouted = select_rerouted(cohort, world, 20.0)
    outcomes = _project_outcomes(world, candidate, cohort, rerouted)
    by_id = {t.transaction_id: t for t in cohort}

    for o in outcomes:
        original = by_id[o.transaction_id]
        if o.rerouted:
            assert o.projected_gateway_id == "gateway_A"
        else:
            # Byte-identical reuse — a reroute must not perturb traffic it did not move.
            assert o.projected_gateway_id == original.gateway_id
            assert o.projected_status == original.current_status
            assert o.projected_latency_ms == original.current_latency_ms


def test_approach_b_holds_under_every_candidate(day4_session, world, ids):
    """An observed FAILED transaction is never rescued by a reroute."""
    cohort = affected_cohort(world)
    for pct in (10.0, 20.0, 30.0):
        candidate = Candidate(
            action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
            traffic_percentage=pct, source_gateway_id=world.affected_gateway_id,
        )
        outcomes = _project_outcomes(
            world, candidate, cohort, select_rerouted(cohort, world, pct)
        )
        for o in outcomes:
            if o.observed_status == "FAILED":
                assert o.projected_status == "FAILED", (
                    "a reroute rescued an OBSERVED failure — Approach B violated"
                )


def test_counterfactual_holds_the_population_constant(day4_session, world, ids):
    _, analysis_run_id = ids
    sizes = set()
    for candidate in [Candidate(action_type=ACTION_NO_ACTION)] + build_candidate_set(world)[:3]:
        sim = run_counterfactual(day4_session, world, analysis_run_id, candidate)
        sizes.add(sim.affected_population)
    assert len(sizes) == 1, "the affected population changed between candidates"


def test_changed_variables_names_only_traffic_allocation(day4_session, world, ids):
    """The counterfactual's self-audit must never grow a second independent variable."""
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id,
        Candidate(action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
                  traffic_percentage=20.0, source_gateway_id=world.affected_gateway_id),
    )
    assert set(sim.changed_variables) == {"traffic_allocation"}


def test_held_constant_records_the_full_invariant_set(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    held = sim.held_constant
    for key in (
        "transaction_population", "transaction_amounts", "cohort_definition",
        "incident_window", "incident_multipliers", "gateway_health", "gateway_profiles",
        "model_version", "seed_semantics", "eligibility_assumptions",
    ):
        assert key in held, f"held_constant omits {key}"


def test_simulation_is_idempotent_on_identical_inputs(day4_session, world, ids):
    _, analysis_run_id = ids
    c = Candidate(action_type=ACTION_NO_ACTION)
    first = run_counterfactual(day4_session, world, analysis_run_id, c)
    second = run_counterfactual(day4_session, world, analysis_run_id, c)
    assert first.simulation_id == second.simulation_id


def test_simulation_fingerprint_is_stable_across_runs(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id,
        Candidate(action_type=ACTION_REROUTE, target_gateway_id="gateway_B",
                  traffic_percentage=10.0, source_gateway_id=world.affected_gateway_id),
    )
    cohort = affected_cohort(world)
    candidate = Candidate(
        action_type=ACTION_REROUTE, target_gateway_id="gateway_B",
        traffic_percentage=10.0, source_gateway_id=world.affected_gateway_id,
    )
    from aventum_counterfactual.fingerprint import compute_simulation_fingerprint

    outcomes = _project_outcomes(world, candidate, cohort, select_rerouted(cohort, world, 10.0))
    rendered = [
        f"{o.transaction_id}|{o.projected_gateway_id}|{o.projected_status}|"
        f"{o.projected_response_code}|{o.projected_latency_regime}|{o.projected_latency_ms:.2f}|"
        f"{int(o.rerouted)}"
        for o in outcomes
    ]
    assert compute_simulation_fingerprint(rendered) == sim.simulation_fingerprint


# ==========================================================================
# SIMULATION_INVALID — a reason, never a number
# ==========================================================================


def test_traffic_above_the_ceiling_is_invalid_not_clamped(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id,
        Candidate(action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
                  traffic_percentage=MAX_TRAFFIC_PERCENTAGE + 10,
                  source_gateway_id=world.affected_gateway_id),
    )
    assert sim.status == STATUS_INVALID
    assert sim.invalid_reason == INVALID_TRAFFIC_EXCEEDS_MAX
    # Crucially: no number was invented in place of the refusal.
    assert sim.projected_gmv_retained is None
    assert sim.projected_success_rate is None


def test_unhealthy_target_yields_invalid_simulation(day4_session, world, ids):
    """Red-team 4: a degraded target cannot be projected onto."""
    _, analysis_run_id = ids
    from aventum_counterfactual.source import GatewayHealthWindow

    world.health["gateway_A"] = [
        GatewayHealthWindow(
            gateway_id="gateway_A", health_state="DEGRADED",
            valid_from=world.window_start - timedelta(days=1),
            valid_to=world.window_end + timedelta(days=1),
            failure_multiplier=3.0, latency_multiplier=2.0, timeout_multiplier=4.0,
        )
    ]
    sim = run_counterfactual(
        day4_session, world, analysis_run_id,
        Candidate(action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
                  traffic_percentage=20.0, source_gateway_id=world.affected_gateway_id),
    )
    assert sim.status == STATUS_INVALID
    assert sim.invalid_reason == INVALID_TARGET_NOT_HEALTHY
    assert sim.projected_gmv_retained is None


def test_invalid_candidates_never_become_recommendations(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id,
        Candidate(action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
                  traffic_percentage=99.0, source_gateway_id=world.affected_gateway_id),
    )
    result = build_recommendation(
        day4_session, simulation_id=sim.simulation_id, analysis_run_id=analysis_run_id,
        world=world, alert_role="PRIMARY",
    )
    assert not result.permitted
    assert result.recommendation.status == "BLOCKED"


# ==========================================================================
# Business impact
# ==========================================================================


def test_gmv_derives_from_observed_amounts(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    observed_total = day4_session.execute(
        text(
            "SELECT SUM(t.amount) FROM transactions t "
            "JOIN simulated_incident_outcomes o ON o.transaction_id = t.transaction_id "
            "WHERE o.incident_id = :iid AND o.in_affected_cohort = true"
        ),
        {"iid": world.incident_id},
    ).scalar()
    assert float(sim.projected_gmv_total) == pytest.approx(float(observed_total), abs=0.01)


def test_expected_gmv_retained_matches_the_contract_formula(world):
    """Σ amount × (P_success(target) − P_success(current)) over rerouted rows only."""
    cohort = affected_cohort(world)
    candidate = Candidate(
        action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
        traffic_percentage=20.0, source_gateway_id=world.affected_gateway_id,
    )
    rerouted = select_rerouted(cohort, world, 20.0)
    outcomes = _project_outcomes(world, candidate, cohort, rerouted)
    impact = compute_business_impact(world, candidate, outcomes)

    expected = sum(
        o.amount * (o.p_success_projected - o.p_success_current) for o in outcomes if o.rerouted
    )
    assert impact.expected_gmv_retained == pytest.approx(expected)
    # Non-rerouted rows contribute exactly zero.
    assert all(
        o.p_success_projected == o.p_success_current for o in outcomes if not o.rerouted
    )


def test_no_action_retains_zero_gmv_by_construction(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    assert float(sim.projected_gmv_retained) == 0.0
    assert float(sim.expected_success_delta) == 0.0


def test_concentration_is_measured_over_the_full_window(world):
    cohort = affected_cohort(world)
    candidate = Candidate(
        action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
        traffic_percentage=30.0, source_gateway_id=world.affected_gateway_id,
    )
    rerouted = select_rerouted(cohort, world, 30.0)
    impact = compute_business_impact(
        world, candidate, _project_outcomes(world, candidate, cohort, rerouted)
    )
    assert sum(impact.current_distribution.values()) == len(world.transactions)
    assert sum(impact.projected_distribution.values()) == len(world.transactions)
    assert impact.concentration_after > impact.concentration_before


def test_capacity_utilization_is_always_null(day4_session, world, ids):
    """P1-1: no capacity telemetry exists, so none may ever be reported."""
    _, analysis_run_id = ids
    sweep = run_candidate_sweep(day4_session, world, analysis_run_id)
    for sim in [sweep.no_action] + sweep.candidates:
        assert sim.capacity_utilization is None
        assert sim.assumptions["capacity"] == CAPACITY_UNAVAILABLE


def test_eligibility_is_reported_as_unconditional(day4_session, world, ids):
    """P1-2: eligibility_conditions is NULL, so no richer model may be implied."""
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    for _, entry in sim.eligibility_result.items():
        assert entry["basis"] == ELIGIBILITY_UNCONDITIONAL


# ==========================================================================
# Risk
# ==========================================================================


def test_risk_is_deterministic(world):
    cohort = affected_cohort(world)
    candidate = Candidate(
        action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
        traffic_percentage=20.0, source_gateway_id=world.affected_gateway_id,
    )
    outcomes = _project_outcomes(world, candidate, cohort, select_rerouted(cohort, world, 20.0))
    impact = compute_business_impact(world, candidate, outcomes)
    a = compute_risk(world, candidate, outcomes, impact)
    b = compute_risk(world, candidate, outcomes, impact)
    assert a.as_dict() == b.as_dict()


def test_risk_components_are_reported_separately(world):
    cohort = affected_cohort(world)
    candidate = Candidate(action_type=ACTION_NO_ACTION)
    outcomes = _project_outcomes(world, candidate, cohort, set())
    impact = compute_business_impact(world, candidate, outcomes)
    components = compute_risk(world, candidate, outcomes, impact).as_dict()
    for key in (
        "concentration_risk", "target_health_risk", "latency_risk",
        "simulation_quality_risk", "evidence_uncertainty_risk", "routing_uncertainty_risk",
    ):
        assert isinstance(components[key], float)


def test_capacity_risk_is_unavailable_not_zero(world):
    """An unmeasurable component must never be silently reported as 'no risk'."""
    cohort = affected_cohort(world)
    candidate = Candidate(action_type=ACTION_NO_ACTION)
    outcomes = _project_outcomes(world, candidate, cohort, set())
    impact = compute_business_impact(world, candidate, outcomes)
    components = compute_risk(world, candidate, outcomes, impact).as_dict()
    assert components["capacity_risk"] == CAPACITY_UNAVAILABLE
    assert components["capacity_risk"] != 0.0


def test_unhealthy_target_drives_health_risk_to_one(world):
    from aventum_counterfactual.source import GatewayHealthWindow

    world.health["gateway_A"] = [
        GatewayHealthWindow(
            gateway_id="gateway_A", health_state="DEGRADED",
            valid_from=world.window_start, valid_to=world.window_end,
            failure_multiplier=3.0, latency_multiplier=2.0, timeout_multiplier=4.0,
        )
    ]
    cohort = affected_cohort(world)
    candidate = Candidate(
        action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
        traffic_percentage=10.0, source_gateway_id=world.affected_gateway_id,
    )
    outcomes = _project_outcomes(world, candidate, cohort, select_rerouted(cohort, world, 10.0))
    impact = compute_business_impact(world, candidate, outcomes)
    assert compute_risk(world, candidate, outcomes, impact).target_health_risk == 1.0


# ==========================================================================
# Optimization
# ==========================================================================


def test_sweep_always_simulates_no_action_first(day4_session, world, ids):
    _, analysis_run_id = ids
    sweep = run_candidate_sweep(day4_session, world, analysis_run_id)
    assert sweep.no_action.action_type == ACTION_NO_ACTION
    assert sweep.no_action.status == STATUS_VALID
    assert sweep.no_action.simulation_id < min(c.simulation_id for c in sweep.candidates)


def test_sweep_evaluates_10_20_30_for_every_eligible_target(day4_session, world, ids):
    _, analysis_run_id = ids
    sweep = run_candidate_sweep(day4_session, world, analysis_run_id)
    percentages = {float(c.traffic_percentage) for c in sweep.candidates}
    assert percentages == {10.0, 20.0, 30.0}
    targets = {c.target_gateway_id for c in sweep.candidates}
    assert world.affected_gateway_id not in targets


def test_no_action_wins_when_no_candidate_clears_the_margin(day4_session, world, ids):
    """NO_ACTION is a SUCCESSFUL outcome, and it must be reachable."""
    _, analysis_run_id = ids
    sweep = run_candidate_sweep(day4_session, world, analysis_run_id)
    # Raise the bar above every candidate's benefit.
    highest = max(float(c.projected_gmv_retained or 0) for c in sweep.valid_candidates)
    best, reason = select_best(sweep.no_action, sweep.candidates, no_action_margin=highest + 1)
    assert best.action_type == ACTION_NO_ACTION
    assert "below the" in reason


def test_no_action_wins_when_every_candidate_is_invalid(day4_session, world, ids):
    _, analysis_run_id = ids
    sweep = run_candidate_sweep(day4_session, world, analysis_run_id)
    for c in sweep.candidates:
        c.status = STATUS_INVALID
        c.invalid_reason = "TARGET_NOT_HEALTHY"
    best, reason = select_best(sweep.no_action, sweep.candidates)
    assert best.action_type == ACTION_NO_ACTION
    assert "valid controlled counterfactual" in reason


class _FakeSim:
    """Minimal stand-in for exercising the selection rule in isolation."""

    def __init__(self, sid, key, gmv, delta, shift):
        self.simulation_id = sid
        self.candidate_key = key
        self.status = STATUS_VALID
        self.action_type = ACTION_REROUTE
        self.projected_gmv_retained = gmv
        self.expected_success_delta = delta
        self.traffic_percentage = shift
        self.invalid_reason = None


def test_tie_break_prefers_the_smallest_shift_reaching_95_percent():
    """The least-intervention rule: 96% of the benefit at a third of the traffic wins."""
    no_action = _FakeSim(0, ACTION_NO_ACTION, 0.0, 0.0, 0.0)
    no_action.action_type = ACTION_NO_ACTION
    small = _FakeSim(1, "REROUTE@10", 9_600.0, 0.03, 10.0)
    large = _FakeSim(2, "REROUTE@30", 10_000.0, 0.04, 30.0)
    best, reason = select_best(no_action, [small, large])
    assert best.simulation_id == small.simulation_id
    assert "smaller" in reason


def test_tie_break_does_not_fire_below_95_percent():
    no_action = _FakeSim(0, ACTION_NO_ACTION, 0.0, 0.0, 0.0)
    no_action.action_type = ACTION_NO_ACTION
    small = _FakeSim(1, "REROUTE@10", 5_000.0, 0.01, 10.0)
    large = _FakeSim(2, "REROUTE@30", 10_000.0, 0.04, 30.0)
    best, _ = select_best(no_action, [small, large])
    assert best.simulation_id == large.simulation_id


def test_selection_is_deterministic_across_repeated_sweeps(day4_session, world, ids):
    _, analysis_run_id = ids
    first = run_candidate_sweep(day4_session, world, analysis_run_id)
    second = run_candidate_sweep(day4_session, world, analysis_run_id)
    assert first.best.simulation_id == second.best.simulation_id
    assert first.selection_reason == second.selection_reason


# ==========================================================================
# Policy gate
# ==========================================================================


def test_golden_scenario_passes_every_gate(day4_session, world, ids):
    _, analysis_run_id = ids
    sweep = run_candidate_sweep(day4_session, world, analysis_run_id)
    decision = validate(sweep.best, load_rca(day4_session, analysis_run_id), world, "PRIMARY")
    assert decision.permitted, decision.reason_codes
    assert len(decision.gates) == 13


def test_every_evidence_gate_fails_closed_independently(day4_session, world, ids):
    """
    Each of the Day 3 quartet must be able to block ON ITS OWN.

    That is the P1-2 property: no single strong signal can carry a weak one.
    """
    _, analysis_run_id = ids
    sweep = run_candidate_sweep(day4_session, world, analysis_run_id)
    base = load_rca(day4_session, analysis_run_id)

    cases = [
        ({"verdict": "UNCERTAIN"}, RCA_NOT_CONFIDENT),
        ({"confidence": 0.10}, CONFIDENCE_BELOW_THRESHOLD),
        ({"evidence_strength": 0.10}, EVIDENCE_STRENGTH_BELOW_THRESHOLD),
        ({"significance_sigma": 1.0}, SIGNIFICANCE_BELOW_THRESHOLD),
        ({"severity": "LOW"}, SEVERITY_BELOW_THRESHOLD),
    ]
    for override, expected_code in cases:
        rca = dict(base)
        rca.update(override)
        decision = validate(sweep.best, rca, world, "PRIMARY")
        assert not decision.permitted
        assert expected_code in decision.reason_codes


def test_derivative_alert_is_blocked(day4_session, world, ids):
    """Red-team: Day 3's P1-1 fix must not be re-opened at the action boundary."""
    _, analysis_run_id = ids
    sweep = run_candidate_sweep(day4_session, world, analysis_run_id)
    decision = validate(
        sweep.best, load_rca(day4_session, analysis_run_id), world, "DERIVATIVE"
    )
    assert ALERT_NOT_PRIMARY in decision.reason_codes


def test_missing_rca_fails_closed(day4_session, world, ids):
    _, analysis_run_id = ids
    sweep = run_candidate_sweep(day4_session, world, analysis_run_id)
    decision = validate(sweep.best, None, world, "PRIMARY")
    assert not decision.permitted


def test_no_action_is_permitted_even_with_weak_evidence(day4_session, world, ids):
    """
    Doing nothing needs no evidentiary case.

    If evidence gates applied to NO_ACTION, weak evidence would block the SAFE option
    and leave the system with nothing honest to recommend.
    """
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    decision = validate(sim, {"verdict": "INSUFFICIENT_EVIDENCE", "confidence": 0.0}, world, None)
    assert decision.permitted


def test_stale_simulation_is_detected_by_rederivation(day4_session, world, ids):
    """Red-team 3/10: freshness is derived, so editing a status column cannot fake it."""
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    # Move the world: change the incident window.
    world.window_end = world.window_end + timedelta(hours=1)
    decision = validate(sim, load_rca(day4_session, analysis_run_id), world, "PRIMARY")
    assert STALE_SIMULATION in decision.reason_codes


def test_thresholds_cannot_be_supplied_by_a_caller():
    """The gate's signature carries no threshold parameter. That IS the mechanism."""
    params = set(inspect.signature(validate).parameters)
    forbidden = {
        "min_confidence", "confidence_threshold", "max_traffic_shift", "thresholds",
        "no_action_margin", "max_concentration", "policy", "overrides", "force",
    }
    assert not (params & forbidden), f"validate() exposes a tunable threshold: {params}"


def test_concentration_bound_blocks_an_over_concentrated_candidate(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id,
        Candidate(action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
                  traffic_percentage=20.0, source_gateway_id=world.affected_gateway_id),
    )
    sim.concentration_after = MAX_CONCENTRATION_AFTER + 0.05
    decision = validate(sim, load_rca(day4_session, analysis_run_id), world, "PRIMARY")
    assert CONCENTRATION_EXCEEDS_BOUND in decision.reason_codes


def test_benefit_below_margin_blocks(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id,
        Candidate(action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
                  traffic_percentage=20.0, source_gateway_id=world.affected_gateway_id),
    )
    sim.projected_gmv_retained = 1.0
    decision = validate(sim, load_rca(day4_session, analysis_run_id), world, "PRIMARY")
    assert BENEFIT_BELOW_NO_ACTION_MARGIN in decision.reason_codes


# ==========================================================================
# Recommendation integrity — the anti-fabrication property
# ==========================================================================


def test_builder_signature_accepts_no_numeric_field():
    """
    RED-TEAM 1, tested structurally.

    A caller cannot supply a fabricated number because no parameter exists to carry one.
    """
    params = inspect.signature(build_recommendation).parameters
    forbidden = {
        "expected_gmv_retained", "expected_success_delta", "expected_latency_delta_ms",
        "risk_score", "confidence", "evidence_strength", "significance_sigma",
        "severity", "traffic_percentage", "projected_gmv_retained", "gmv", "benefit",
        "metrics", "numbers",
    }
    assert not (set(params) & forbidden), f"builder exposes a numeric field: {set(params)}"
    # The only content parameter is qualitative.
    assert "rationale" in params
    assert "simulation_id" in params


def test_injecting_a_number_into_the_builder_raises(day4_session, world, ids):
    """Adversarial: passing a figure is a TypeError, not a silently ignored kwarg."""
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    with pytest.raises(TypeError):
        build_recommendation(
            day4_session, simulation_id=sim.simulation_id, analysis_run_id=analysis_run_id,
            world=world, alert_role="PRIMARY",
            expected_gmv_retained=999_999.0,  # no such parameter
        )


def test_recommendation_numbers_equal_the_simulation_row(day4_session, world, ids):
    _, analysis_run_id = ids
    sweep = run_candidate_sweep(day4_session, world, analysis_run_id)
    result = build_recommendation(
        day4_session, simulation_id=sweep.best.simulation_id,
        analysis_run_id=analysis_run_id, world=world, alert_role="PRIMARY",
    )
    rec, sim = result.recommendation, sweep.best
    assert rec.expected_gmv_retained == sim.projected_gmv_retained
    assert rec.expected_success_delta == sim.expected_success_delta
    assert rec.expected_latency_delta_ms == sim.latency_delta_ms
    assert rec.risk_score == sim.risk_score
    assert rec.traffic_percentage == sim.traffic_percentage


def test_day4a_recommendation_carries_no_rationale(day4_session, world, ids):
    """The deterministic spine must not depend on narrative. No agent exists."""
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    assert result.recommendation.rationale is None
    assert result.recommendation.agent_run_id is None


def test_recommendation_is_idempotent(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    a = build_recommendation(day4_session, simulation_id=sim.simulation_id,
                             analysis_run_id=analysis_run_id, world=world, alert_role="PRIMARY")
    b = build_recommendation(day4_session, simulation_id=sim.simulation_id,
                             analysis_run_id=analysis_run_id, world=world, alert_role="PRIMARY")
    assert a.recommendation.recommendation_id == b.recommendation.recommendation_id


def test_state_machine_is_forward_only(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    rec = result.recommendation
    with pytest.raises(RecommendationStateError):
        advance_status(rec, "DRAFT")


def test_blocked_recommendation_is_terminal(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id,
        Candidate(action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
                  traffic_percentage=99.0, source_gateway_id=world.affected_gateway_id),
    )
    result = build_recommendation(
        day4_session, simulation_id=sim.simulation_id, analysis_run_id=analysis_run_id,
        world=world, alert_role="PRIMARY",
    )
    assert result.recommendation.status == "BLOCKED"
    with pytest.raises(RecommendationStateError):
        advance_status(result.recommendation, "AWAITING_APPROVAL")


def test_no_action_requires_no_approval(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    result = build_recommendation(
        day4_session, simulation_id=sim.simulation_id, analysis_run_id=analysis_run_id,
        world=world, alert_role="PRIMARY",
    )
    assert result.recommendation.status == "PERMITTED"
    assert not requires_approval(result.recommendation)
    with pytest.raises(ApprovalError):
        request_approval(day4_session, result.recommendation)


# ==========================================================================
# Approval
# ==========================================================================


def test_approval_payload_is_decision_complete(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    payload = build_approval_payload(day4_session, result.recommendation, result.decision)
    for key in (
        "proposed_action", "source_gateway", "target_gateway", "traffic_percentage",
        "expected_benefit", "expected_risk", "decision_inputs", "evidence_refs",
        "simulation_id", "alternatives_rejected", "gates", "expires_at", "provenance",
    ):
        assert key in payload, f"approval payload omits {key}"
    assert payload["provenance"] == "SYNTHETIC_INCIDENT / SIMULATED_EXECUTION"
    assert payload["expected_risk"]["capacity"] == CAPACITY_UNAVAILABLE
    # The Day 3 quartet is presented separately, never as one score.
    assert set(payload["decision_inputs"]) == {
        "confidence", "evidence_strength", "significance_sigma", "severity"
    }


def test_approval_requires_a_human_identity(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    approval = request_approval(day4_session, result.recommendation, result.decision)
    with pytest.raises(ApprovalError):
        decide_approval(day4_session, approval, decision="APPROVED", approver_identity="")


def test_only_one_pending_approval_per_recommendation(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    request_approval(day4_session, result.recommendation, result.decision)
    with pytest.raises(ApprovalError):
        request_approval(day4_session, result.recommendation, result.decision)


def test_pending_uniqueness_is_enforced_by_the_database(day4_session, world, ids):
    """The partial unique index, not just the application check."""
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    first = request_approval(day4_session, result.recommendation, result.decision)
    day4_session.flush()
    with pytest.raises(IntegrityError):
        day4_session.add(
            Approval(
                recommendation_id=first.recommendation_id, status="PENDING",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
                approval_fingerprint="f" * 64,
            )
        )
        day4_session.flush()
    day4_session.rollback()


def test_expired_approval_cannot_be_decided(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    approval = request_approval(day4_session, result.recommendation, result.decision)
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    with pytest.raises(ApprovalError):
        decide_approval(
            day4_session, approval, decision="APPROVED",
            approver_identity="tester", now=later,
        )
    assert approval.status == "EXPIRED"


def test_decisions_are_terminal(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    approval = request_approval(day4_session, result.recommendation, result.decision)
    decide_approval(day4_session, approval, decision="APPROVED", approver_identity="a")
    with pytest.raises(ApprovalError):
        decide_approval(day4_session, approval, decision="REJECTED", approver_identity="b")


def test_blocked_recommendation_is_never_presented_for_approval(day4_session, world, ids):
    _, analysis_run_id = ids
    sim = run_counterfactual(
        day4_session, world, analysis_run_id,
        Candidate(action_type=ACTION_REROUTE, target_gateway_id="gateway_A",
                  traffic_percentage=99.0, source_gateway_id=world.affected_gateway_id),
    )
    result = build_recommendation(
        day4_session, simulation_id=sim.simulation_id, analysis_run_id=analysis_run_id,
        world=world, alert_role="PRIMARY",
    )
    with pytest.raises(ApprovalError):
        request_approval(day4_session, result.recommendation)


# ==========================================================================
# Execution and revalidation
# ==========================================================================


def test_execution_without_approval_is_blocked(day4_session, world, ids):
    """Red-team 2."""
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    approval = request_approval(day4_session, result.recommendation, result.decision)
    # Deliberately NOT decided — still PENDING.
    outcome = execute_action(
        day4_session, recommendation_id=result.recommendation.recommendation_id,
        approval_id=approval.approval_id, world=world, alert_role="PRIMARY",
    )
    assert not outcome.executed
    assert outcome.rejection_reason == RECOMMENDATION_NOT_APPROVED
    assert outcome.action.actual_simulated_outcome is None


def test_full_flow_executes_and_records_measurable_outcome(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    flow = run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    action = flow.action
    assert flow.execution.executed
    assert action.status == "EXECUTED"
    assert action.adapter_name == SIMULATED_ADAPTER_NAME
    result = action.actual_simulated_outcome
    # Measurable, not a bare success flag.
    for key in (
        "traffic_moved", "resulting_allocation", "post_action_success_rate",
        "post_action_failure_count", "post_action_gmv_at_risk", "post_action_latency_p95",
        "execution_fingerprint",
    ):
        assert key in result


def test_expected_and_actual_outcomes_are_stored_separately(day4_session, world, ids):
    """Day 5 measures the GAP between these; merging them would erase it."""
    incident_id, analysis_run_id = ids
    flow = run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    action = flow.action
    assert action.expected_outcome is not None
    assert action.actual_simulated_outcome is not None
    assert action.expected_outcome != action.actual_simulated_outcome
    assert "projected_gmv_retained" in action.expected_outcome
    assert "post_action_success_rate" in action.actual_simulated_outcome


def test_execution_revalidates_and_records_every_check(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    flow = run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    checks = {c["check"] for c in flow.action.revalidation_result["checks"]}
    for name in (
        "recommendation_exists", "recommendation_approved", "approval_exists",
        "approval_approved", "approval_not_expired", "recommendation_not_expired",
        "approval_fingerprint_matches", "simulation_valid", "simulation_fresh",
        "policy_version_unchanged", "policy_revalidated", "target_healthy",
        "target_eligible", "idempotency_key_claimed",
    ):
        assert name in checks, f"revalidation omitted {name}"


def test_stale_world_blocks_execution(day4_session, world, ids):
    """Red-team 3: the world moved after approval."""
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    approval = request_approval(day4_session, result.recommendation, result.decision)
    decide_approval(day4_session, approval, decision="APPROVED", approver_identity="tester")

    world.window_end = world.window_end + timedelta(hours=3)  # the world moved
    outcome = execute_action(
        day4_session, recommendation_id=result.recommendation.recommendation_id,
        approval_id=approval.approval_id, world=world, alert_role="PRIMARY",
    )
    assert not outcome.executed
    assert outcome.rejection_reason == STALE_SIMULATION


def test_edited_recommendation_breaks_the_approval_fingerprint(day4_session, world, ids):
    """Red-team 8: an approval is not transferable to modified content."""
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    approval = request_approval(day4_session, result.recommendation, result.decision)
    decide_approval(day4_session, approval, decision="APPROVED", approver_identity="tester")

    result.recommendation.recommendation_fingerprint = "0" * 64
    day4_session.flush()

    outcome = execute_action(
        day4_session, recommendation_id=result.recommendation.recommendation_id,
        approval_id=approval.approval_id, world=world, alert_role="PRIMARY",
    )
    assert not outcome.executed
    assert outcome.rejection_reason == APPROVAL_FINGERPRINT_MISMATCH


def test_unhealthy_target_blocks_execution(day4_session, world, ids):
    """Red-team 4, at the execution boundary rather than the simulation boundary."""
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    approval = request_approval(day4_session, result.recommendation, result.decision)
    decide_approval(day4_session, approval, decision="APPROVED", approver_identity="tester")

    from aventum_counterfactual.source import GatewayHealthWindow

    target = result.recommendation.target_gateway_id
    world.health[target] = [
        GatewayHealthWindow(
            gateway_id=target, health_state="DEGRADED",
            valid_from=world.window_start, valid_to=world.window_end,
            failure_multiplier=3.0, latency_multiplier=2.0, timeout_multiplier=4.0,
        )
    ]
    outcome = execute_action(
        day4_session, recommendation_id=result.recommendation.recommendation_id,
        approval_id=approval.approval_id, world=world, alert_role="PRIMARY",
    )
    assert not outcome.executed
    # Freshness is checked before health, and a health change also moves the
    # fingerprint — either rejection proves the stale world was caught.
    assert outcome.rejection_reason in (TARGET_NOT_HEALTHY, STALE_SIMULATION)


def test_expired_approval_blocks_execution(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    approval = request_approval(day4_session, result.recommendation, result.decision)
    decide_approval(day4_session, approval, decision="APPROVED", approver_identity="tester")
    approval.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    day4_session.flush()

    outcome = execute_action(
        day4_session, recommendation_id=result.recommendation.recommendation_id,
        approval_id=approval.approval_id, world=world, alert_role="PRIMARY",
    )
    assert not outcome.executed
    assert outcome.rejection_reason == APPROVAL_EXPIRED


def test_rejected_execution_never_invokes_the_adapter(day4_session, world, ids):
    """No partial execution: a refusal must not reach the adapter at all."""
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    approval = request_approval(day4_session, result.recommendation, result.decision)

    class _ExplodingAdapter:
        name = SIMULATED_ADAPTER_NAME

        def apply(self, action):  # pragma: no cover - must never run
            raise AssertionError("adapter invoked despite a failed revalidation")

    outcome = execute_action(
        day4_session, recommendation_id=result.recommendation.recommendation_id,
        approval_id=approval.approval_id, world=world, alert_role="PRIMARY",
        adapter=_ExplodingAdapter(),
    )
    assert not outcome.executed


def test_simulated_adapter_satisfies_the_protocol():
    assert isinstance(SimulatedRoutingAdapter(), RoutingActionAdapter)


# ==========================================================================
# Idempotency and concurrency
# ==========================================================================


def test_idempotency_key_matches_the_contract_formula():
    import hashlib

    expected = hashlib.sha256("7|9|SimulatedRoutingAdapter".encode()).hexdigest()
    assert compute_idempotency_key(7, 9, SIMULATED_ADAPTER_NAME) == expected


def test_repeated_execution_returns_the_original_result(day4_session, world, ids):
    """Red-team 6, single-threaded."""
    incident_id, analysis_run_id = ids
    flow = run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    first = flow.action

    second = execute_action(
        day4_session, recommendation_id=first.recommendation_id,
        approval_id=first.approval_id, world=world, alert_role="PRIMARY",
    )
    assert second.duplicate
    assert second.action.action_id == first.action_id
    assert second.action.actual_simulated_outcome == first.actual_simulated_outcome


def test_duplicate_suppression_is_audited(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    flow = run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    execute_action(
        day4_session, recommendation_id=flow.action.recommendation_id,
        approval_id=flow.action.approval_id, world=world, alert_role="PRIMARY",
    )
    day4_session.flush()
    events = day4_session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE event_type = :t"),
        {"t": ACTION_DUPLICATE_SUPPRESSED},
    ).scalar()
    assert events >= 1


def test_concurrent_execution_runs_the_adapter_exactly_once(
    test_database_url, day4_session, world, ids
):
    """
    RED-TEAM 6, with real threads and real sessions.

    The guarantee must come from the UNIQUE constraint, not from timing luck, so both
    threads race on the same idempotency key against the real database. A counting
    adapter proves the adapter body ran once; the action count proves the database
    admitted exactly one row.
    """
    incident_id, analysis_run_id = ids
    result = run_decision_pipeline(day4_session, incident_id, analysis_run_id)
    approval = request_approval(day4_session, result.recommendation, result.decision)
    decide_approval(day4_session, approval, decision="APPROVED", approver_identity="tester")
    recommendation_id = result.recommendation.recommendation_id
    approval_id = approval.approval_id
    day4_session.commit()

    invocations: list[int] = []
    lock = threading.Lock()

    class _CountingAdapter(SimulatedRoutingAdapter):
        def apply(self, action):
            with lock:
                invocations.append(1)
            return super().apply(action)

    barrier = threading.Barrier(2)
    outcomes: list = []
    errors: list = []
    # Built from the plain URL string, not from the live engine: str(engine.url)
    # masks the password, which would make each worker fail to authenticate.
    factory = build_session_factory(build_engine(test_database_url))

    def worker() -> None:
        try:
            with factory() as session:
                local_world = load_world_state(session, incident_id)
                barrier.wait(timeout=30)
                outcome = execute_action(
                    session, recommendation_id=recommendation_id, approval_id=approval_id,
                    world=local_world, alert_role="PRIMARY", adapter=_CountingAdapter(),
                )
                session.commit()
                outcomes.append(outcome)
        except Exception as exc:  # pragma: no cover - surfaced by the assertions
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)

    assert not errors, f"worker raised: {errors}"
    assert len(outcomes) == 2
    assert sum(invocations) == 1, "the adapter ran more than once"

    rows = day4_session.execute(
        text("SELECT COUNT(*) FROM actions WHERE recommendation_id = :rid"),
        {"rid": recommendation_id},
    ).scalar()
    assert rows == 1, "more than one action row exists for the same idempotency key"
    assert sum(1 for o in outcomes if o.duplicate) == 1


def test_duplicate_action_row_is_rejected_by_the_database(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    flow = run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    with pytest.raises(IntegrityError):
        day4_session.add(
            Action(
                recommendation_id=flow.action.recommendation_id,
                approval_id=flow.action.approval_id,
                idempotency_key=flow.action.idempotency_key,  # duplicate
                adapter_name=SIMULATED_ADAPTER_NAME, status="PENDING",
            )
        )
        day4_session.flush()
    day4_session.rollback()


# ==========================================================================
# Audit, provenance, Day 5 handoff
# ==========================================================================


def test_audit_trail_covers_the_full_chain(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    day4_session.flush()
    types = {
        r[0]
        for r in day4_session.execute(
            text("SELECT DISTINCT event_type FROM audit_events WHERE incident_id = :i"),
            {"i": incident_id},
        ).fetchall()
    }
    for expected in (
        "SIMULATION_COMPLETED", "POLICY_VALIDATED", "RECOMMENDATION_CREATED",
        "APPROVAL_REQUESTED", "APPROVAL_DECIDED", "ACTION_EXECUTED",
    ):
        assert expected in types, f"audit trail missing {expected}"


def test_audit_records_the_human_actor(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="alice")
    day4_session.flush()
    actor = day4_session.execute(
        text("SELECT actor FROM audit_events WHERE event_type = 'APPROVAL_DECIDED'")
    ).scalar()
    assert actor == "HUMAN:alice"


def test_audit_stores_references_not_row_copies(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    day4_session.flush()
    row = day4_session.execute(
        text(
            "SELECT output_ref FROM audit_events "
            "WHERE event_type = 'RECOMMENDATION_CREATED' LIMIT 1"
        )
    ).scalar()
    assert set(row) == {"table", "id"}


def test_audit_never_stores_chain_of_thought(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    day4_session.flush()
    payloads = day4_session.execute(
        text("SELECT payload::text FROM audit_events WHERE incident_id = :i"),
        {"i": incident_id},
    ).fetchall()
    for (blob,) in payloads:
        lowered = (blob or "").lower()
        for banned in ("chain_of_thought", "reasoning_trace", "<think>", "thinking"):
            assert banned not in lowered


def test_provenance_chain_reaches_the_source_hash(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    flow = run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    chain = provenance_chain(day4_session, flow.action.action_id)
    for key in (
        "action_id", "approval_id", "recommendation_id", "simulation_id",
        "analysis_run_id", "incident_id", "generation_run_id", "generation_fingerprint",
        "source_ingestion_run_id", "canonical_fingerprint", "source_sha256",
    ):
        assert chain[key] is not None, f"provenance chain broken at {key}"
    # Named `answer_key` rather than the literal table name so the AST isolation guard
    # above can stay strict with zero exemptions.
    assert chain["layers"]["answer_key"].startswith("EXCLUDED")


def test_day5_handoff_carries_every_required_field(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    flow = run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    handoff = build_verification_handoff(day4_session, flow.action.action_id).as_dict()
    for key in (
        "action_id", "recommendation_id", "approval_id", "incident_id", "simulation_id",
        "pre_action_metrics", "expected_outcome", "actual_simulated_outcome",
        "cohort_definition", "measurement_window", "audit_event_ids",
    ):
        assert handoff[key] is not None, f"Day 5 handoff missing {key}"
    assert handoff["audit_event_ids"]
    assert handoff["provenance"] == "SYNTHETIC_INCIDENT / SIMULATED_EXECUTION"


def test_day5_handoff_makes_no_recovery_claim(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    flow = run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    handoff = build_verification_handoff(day4_session, flow.action.action_id).as_dict()
    assert "NO recovery claim" in handoff["verification_note"]
    blob = str(handoff).lower()
    assert "recovered gmv" not in blob


def test_rollback_is_a_forward_transition(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    flow = run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    action = rollback(day4_session, flow.action, reason="Day 5 verification failed")
    assert action.status == "ROLLED_BACK"
    # The original row is never deleted — "we acted, then reverted" stays visible.
    assert day4_session.get(Action, action.action_id) is not None
    assert action.actual_simulated_outcome is not None


# ==========================================================================
# Ground-truth isolation — extended to every Day 4 package
# ==========================================================================


def test_no_day4_module_references_the_ground_truth_table():
    """
    The Day 3 AST guard, extended to Day 4 as the database contract requires.

    A behavioural test shows ground truth was unused on one input; this shows it cannot
    be used on any.
    """
    import ast
    import pathlib

    def executable_source(path: pathlib.Path) -> str:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0].value.value = ""
        return ast.unparse(tree)

    backend = pathlib.Path(__file__).resolve().parents[1]
    for package in ("aventum_counterfactual", "aventum_policy", "aventum_action"):
        for module in sorted((backend / package).glob("*.py")):
            source = executable_source(module)
            assert "incident_ground_truth" not in source, f"{module.name} names ground truth"
            assert "IncidentGroundTruth" not in source, f"{module.name} imports ground truth"
            assert "ground_truth" not in source, f"{module.name} touches ground truth"


def test_only_the_source_module_names_synthetic_tables():
    """
    Production substitution: the intelligence and action layers must stay
    telemetry-agnostic, exactly as Day 3's review established.
    """
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[1]
    allowed = {"source.py", "models.py", "handoff.py"}
    for package in ("aventum_counterfactual", "aventum_policy", "aventum_action"):
        for module in sorted((backend / package).glob("*.py")):
            if module.name in allowed:
                continue
            text_ = module.read_text(encoding="utf-8")
            for table in (
                "synthetic_infrastructure_assignments", "synthetic_gateway_profiles",
                "synthetic_gateway_health_states", "simulated_incident_outcomes",
            ):
                assert table not in text_, (
                    f"{package}/{module.name} names {table}; reads must go through source.py"
                )


def test_no_qwen_or_agent_code_exists_in_day4a():
    """Day 4A scope boundary, asserted rather than assumed."""
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[1]
    assert not (backend / "aventum_agent").exists(), "aventum_agent is Day 4B"
    for package in ("aventum_counterfactual", "aventum_policy", "aventum_action"):
        for module in sorted((backend / package).glob("*.py")):
            source = module.read_text(encoding="utf-8").lower()
            for banned in ("import ollama", "from ollama", "qwen3:8b", "http://localhost:11434"):
                assert banned not in source, f"{module.name} contains agent code: {banned}"


# ==========================================================================
# Prior layers unchanged
# ==========================================================================


def test_day4_never_writes_to_observed_transactions(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    before = day4_session.execute(
        text("SELECT md5(string_agg(transaction_id || status || amount::text, '' "
             "ORDER BY transaction_id)) FROM transactions")
    ).scalar()
    run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    day4_session.flush()
    after = day4_session.execute(
        text("SELECT md5(string_agg(transaction_id || status || amount::text, '' "
             "ORDER BY transaction_id)) FROM transactions")
    ).scalar()
    assert before == after


def test_day4_never_writes_to_day3_outcomes(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    before = day4_session.execute(
        text("SELECT md5(string_agg(transaction_id || simulated_status, '' "
             "ORDER BY transaction_id)) FROM simulated_incident_outcomes "
             "WHERE incident_id = :i"),
        {"i": incident_id},
    ).scalar()
    run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    day4_session.flush()
    after = day4_session.execute(
        text("SELECT md5(string_agg(transaction_id || simulated_status, '' "
             "ORDER BY transaction_id)) FROM simulated_incident_outcomes "
             "WHERE incident_id = :i"),
        {"i": incident_id},
    ).scalar()
    assert before == after


def test_day4_never_mutates_the_baseline_synthetic_layer(day4_session, world, ids):
    incident_id, analysis_run_id = ids
    before = day4_session.execute(
        text("SELECT md5(string_agg(gateway_id || baseline_failure_probability::text, '' "
             "ORDER BY gateway_id)) FROM synthetic_gateway_profiles "
             "WHERE profile_version = 'baseline-v1'")
    ).scalar()
    run_full_flow(day4_session, incident_id, analysis_run_id, approver_identity="tester")
    day4_session.flush()
    after = day4_session.execute(
        text("SELECT md5(string_agg(gateway_id || baseline_failure_probability::text, '' "
             "ORDER BY gateway_id)) FROM synthetic_gateway_profiles "
             "WHERE profile_version = 'baseline-v1'")
    ).scalar()
    assert before == after
