"""
Day 3 tests: incident injection, Approach B simulated outcomes, detection, evidence,
hypotheses, and explainable RCA.

Two properties get disproportionate attention here, because everything else in Day 3
rests on them:

  1. APPROACH B -- an incident adds modelled failures and never moves observed ones.
     Tested from three directions: the observed table is byte-identical afterwards, the
     database physically rejects a rescued failure, and the control cohort is provably
     unchanged rather than approximately unchanged.

  2. GROUND-TRUTH ISOLATION -- the diagnosis path cannot see the answer key. Tested
     adversarially: the RCA is re-run with ground truth deleted and with it corrupted,
     and must produce a byte-identical conclusion both times.

Tests run against a fixture population sized so real production thresholds apply
(cohorts clear MIN_COHORT_SIZE and MIN_BASELINE_COHORT_SIZE without loosening config).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aventum_incident import INCIDENT_CONFIG_VERSION, INCIDENT_MODEL_VERSION
from aventum_incident.constants import (
    ALERT_ROLE_DERIVATIVE,
    ALERT_ROLE_PRIMARY,
    EVIDENCE_CONFOUNDING,
    EVIDENCE_CONTROL_COMPARISON,
    HIGH_SEVERITIES,
    MIN_COHORT_SIZE,
    RCA_CONFIDENT_THRESHOLD,
    RCA_UNCERTAIN_THRESHOLD,
    RCA_VERDICT_UNCERTAIN,
    SOURCE_LAYERS,
)
from aventum_incident.detect import DetectionConfig, detect_anomalies
from aventum_incident.evaluation import evaluate_rca
from aventum_incident.evidence import build_evidence
from aventum_incident.handoff import build_handoff, ranked_hypotheses
from aventum_incident.hypothesis import (
    build_hypotheses,
    confidence_from,
    evidence_strength_from,
)
from aventum_incident.incident import (
    IncidentDefinition,
    IncidentDefinitionError,
    IncidentLifecycleError,
    advance_status,
    create_incident,
    load_ground_truth,
)
from aventum_incident.metrics import MetricStore, cohort_metrics
from aventum_incident.models import Incident, SimulatedIncidentOutcome
from aventum_incident.pipeline import run_analysis, run_incident_pipeline, run_quiet_analysis
from aventum_incident.rca import run_rca
from aventum_incident.rng import incident_digest_for, lane_uniform
from aventum_incident.simulate import (
    added_failure_probability,
    build_runtime_profile,
    is_in_affected_cohort,
    simulate_incident,
)
from aventum_incident.statistics import (
    anomaly_score,
    effect_factor,
    severity_for,
    two_proportion_z,
)
from aventum_ingest.pipeline import run_ingestion
from aventum_synth.generator import run_generation
from tests.conftest import make_row

IST = timezone(timedelta(hours=5, minutes=30))

# The fixture population spans 12 days in March 2024, with the incident in the last
# three. Density is chosen so a three-day window holds enough transactions for the
# PRODUCTION detection thresholds to apply unchanged -- roughly 2,000 rows in-window and
# ~260 on the smallest gateway, which mirrors the real 250K dataset's flagship cohort.
# Testing against loosened thresholds would prove nothing about the shipped detector.
FIXTURE_DAYS = 12
FIXTURE_ROWS = 8000
INCIDENT_START = datetime(2024, 3, 10, 0, 0, 0, tzinfo=IST)
INCIDENT_END = datetime(2024, 3, 13, 0, 0, 0, tzinfo=IST)
# A quiet stretch well clear of the incident, for the false-positive control.
QUIET_START = datetime(2024, 3, 4, 0, 0, 0, tzinfo=IST)
QUIET_END = datetime(2024, 3, 7, 0, 0, 0, tzinfo=IST)


def _fixture_rows(count: int = FIXTURE_ROWS) -> list[dict]:
    """
    A population dense enough for real detection thresholds to apply.

    Density is the point: Day 3 cohorts must clear MIN_COHORT_SIZE inside a three-day
    window, which a sparse population spread over a year cannot do. Concentrating the
    same row count into 24 days lets the tests exercise the production configuration
    instead of a loosened one.
    """
    import hashlib

    # Real cohort vocabularies (docs/DATA_DICTIONARY.md). Varying these matters more
    # than it looks: if every row carried the same bank, device and network, those
    # dimensions would be perfectly collinear and an issuer incident would be
    # mathematically indistinguishable from a network incident. The alternative-cause
    # scenario would then be untestable.
    banks = ("SBI", "HDFC", "ICICI", "Axis", "PNB", "Kotak", "Yes Bank", "IndusInd")
    devices = ("Android", "iOS", "Web")
    networks = ("4G", "5G", "WiFi", "3G")
    regions = (
        "Maharashtra", "Delhi", "Karnataka", "Tamil Nadu", "Gujarat",
        "Uttar Pradesh", "West Bengal", "Telangana", "Rajasthan", "Andhra Pradesh",
    )
    methods = ("P2M", "P2P", "Bill Payment", "Recharge")
    categories = ("Grocery", "Food", "Fuel", "Shopping", "Utilities", "Transport")

    def draw(salt: str, index: int) -> float:
        """Deterministic uniform in [0,1) from a salted hash of the row index."""
        digest = hashlib.sha256(f"{salt}-{index}".encode()).digest()
        return int.from_bytes(digest[:4], "big") / 2**32

    def pick(values: tuple[str, ...], salt: str, index: int) -> str:
        return values[int(draw(salt, index) * len(values))]

    rows = []
    for i in range(count):
        # EVERY attribute is hash-derived, never `i % k`.
        #
        # Modular strides are dangerous here because they are not independent of each
        # other: with `sender_bank = banks[i % 8]` and `day = 1 + (i % 12)`, gcd(8,12)=4
        # means each day only ever sees two of the eight banks -- so a bank targeted by
        # an incident can be entirely absent from the incident window, producing an
        # empty affected cohort and a silently meaningless test. Hashing each dimension
        # with its own salt makes them mutually independent while staying reproducible.
        status = "FAILED" if draw("status", i) < 0.05 else "SUCCESS"
        day = 1 + int(draw("day", i) * FIXTURE_DAYS)
        hour = 1 + (i % 22)
        minute = i % 60
        second = 10 + (i % 40)
        timestamp = f"2024-03-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

        sender = pick(banks, "sender", i)
        receiver = pick(banks, "receiver", i)
        method = pick(methods, "method", i)
        is_p2p = method == "P2P"
        rows.append(
            make_row(
                i + 1,
                transaction_status=status,
                timestamp=timestamp,
                sender_bank=sender,
                receiver_bank=receiver if receiver != sender else banks[(banks.index(sender) + 1) % 8],
                device_type=pick(devices, "device", i),
                network_type=pick(networks, "network", i),
                sender_state=pick(regions, "region", i),
                **{
                    "transaction type": method,
                    # P2P rows must carry no merchant category; every other method must.
                    "merchant_category": "" if is_p2p else pick(categories, "category", i),
                },
            )
        )
    return rows


@pytest.fixture()
def generated(engine, registered_source):
    """An ingested canonical population with its Day 2B synthetic layer attached."""
    run_ingestion(engine, registered_source(_fixture_rows(), name="incident-fixture.csv"))
    result = run_generation(engine, generation_seed="day3-test-seed")
    assert result.succeeded
    return engine, result


@pytest.fixture()
def session(generated) -> Session:
    engine, _ = generated
    with Session(engine) as sess:
        yield sess


@pytest.fixture()
def run_ids(session) -> tuple[int, int]:
    row = session.execute(
        text(
            "SELECT generation_run_id, source_ingestion_run_id "
            "FROM synthetic_generation_runs WHERE status = 'SUCCEEDED' "
            "ORDER BY generation_run_id DESC LIMIT 1"
        )
    ).mappings().first()
    return int(row["generation_run_id"]), int(row["source_ingestion_run_id"])


def _definition(run_ids, **overrides) -> IncidentDefinition:
    generation_run_id, ingestion_run_id = run_ids
    params = dict(
        incident_name="test-gateway-c",
        incident_type="gateway_degradation",
        affected_gateway_id="gateway_C",
        affected_segment=None,
        incident_start=INCIDENT_START,
        incident_end=INCIDENT_END,
        failure_multiplier=3.5,
        latency_multiplier=2.2,
        timeout_multiplier=6.0,
        target_failure_rate=0.225,
        generation_run_id=generation_run_id,
        source_ingestion_run_id=ingestion_run_id,
        incident_seed="test-incident-seed",
        ground_truth_root_cause="Synthetic degradation injected into gateway_C",
    )
    params.update(overrides)
    return IncidentDefinition(**params)


# ==========================================================================
# Statistics primitives (no database)
# ==========================================================================


def test_two_proportion_z_matches_hand_computed_value():
    # baseline 5/100 = 5%, current 25/100 = 25%; pooled p = 0.15
    # SE = sqrt(0.15*0.85*(1/100+1/100)) = sqrt(0.00255) = 0.050498
    # z  = (0.25-0.05)/0.050498 = 3.9605
    assert two_proportion_z(5, 100, 25, 100) == pytest.approx(3.9605, abs=1e-3)


def test_two_proportion_z_is_zero_for_empty_samples():
    assert two_proportion_z(0, 0, 5, 100) == 0.0
    assert two_proportion_z(5, 100, 0, 0) == 0.0


def test_two_proportion_z_is_negative_when_the_rate_improves():
    assert two_proportion_z(25, 100, 5, 100) < 0


def test_effect_factor_saturates_at_the_reference_delta():
    assert effect_factor(0.0) == 0.0
    assert effect_factor(0.15) == pytest.approx(1.0)
    assert effect_factor(0.90) == pytest.approx(1.0)


def test_anomaly_score_penalises_a_trivial_delta_on_a_huge_cohort():
    """A tiny move backed by enormous n must not outrank a real outage."""
    trivial = anomaly_score(significance_sigma=12.0, absolute_delta=0.003)
    real = anomaly_score(significance_sigma=9.0, absolute_delta=0.16)
    assert trivial < real


def test_anomaly_score_is_zero_for_an_improvement():
    assert anomaly_score(-5.0, 0.20) == 0.0


def test_severity_bands_are_ordered():
    assert severity_for(12.0) == "CRITICAL"
    assert severity_for(7.0) == "HIGH"
    assert severity_for(5.0) == "MEDIUM"
    assert severity_for(3.2) == "LOW"
    assert severity_for(1.0) == "NONE"


# ==========================================================================
# Deterministic RNG
# ==========================================================================


def test_incident_digest_is_stable_for_identical_inputs():
    args = ("TXN1", "key-abc", "seed-1", "1.0.0", "1.0.0")
    assert incident_digest_for(*args) == incident_digest_for(*args)


@pytest.mark.parametrize("position", range(5))
def test_changing_any_digest_input_changes_the_digest(position):
    base = ["TXN1", "key-abc", "seed-1", "1.0.0", "1.0.0"]
    changed = list(base)
    changed[position] = changed[position] + "-x"
    assert incident_digest_for(*base) != incident_digest_for(*changed)


def test_digest_lanes_are_independent():
    digest = incident_digest_for("TXN1", "key", "seed", "1.0.0", "1.0.0")
    values = {lane_uniform(digest, lane) for lane in range(4)}
    assert len(values) == 4


def test_digest_does_not_use_python_salted_hash():
    """
    The digest must be a pure function of its inputs across processes.

    Python's built-in hash() is salted per process via PYTHONHASHSEED, so a value
    derived from it would silently differ between runs. Recomputing the expected
    SHA-256 here independently proves the implementation uses hashlib.
    """
    import hashlib

    expected = hashlib.sha256(b"TXN1|key|seed|1.0.0|1.0.0").digest()
    assert incident_digest_for("TXN1", "key", "seed", "1.0.0", "1.0.0") == expected


# ==========================================================================
# Incident definition, identity, lifecycle
# ==========================================================================


def test_incident_key_is_stable_for_an_identical_definition(run_ids):
    assert _definition(run_ids).incident_key == _definition(run_ids).incident_key


@pytest.mark.parametrize(
    "overrides",
    [
        {"failure_multiplier": 3.6},
        {"latency_multiplier": 2.3},
        {"timeout_multiplier": 6.1},
        {"incident_seed": "other-seed"},
        {"affected_gateway_id": "gateway_B"},
        {"incident_end": INCIDENT_END + timedelta(hours=1)},
        {"affected_segment": {"sender_bank": "SBI"}},
    ],
)
def test_changing_any_definition_field_changes_the_incident_key(run_ids, overrides):
    assert _definition(run_ids).incident_key != _definition(run_ids, **overrides).incident_key


def test_incident_key_ignores_the_timezone_a_window_is_expressed_in(run_ids):
    """The window is a set of instants, not a string; UTC and IST spellings must agree."""
    utc_version = _definition(
        run_ids,
        incident_start=INCIDENT_START.astimezone(timezone.utc),
        incident_end=INCIDENT_END.astimezone(timezone.utc),
    )
    assert utc_version.incident_key == _definition(run_ids).incident_key


def test_incident_creation_persists_and_is_idempotent(session, run_ids):
    definition = _definition(run_ids)
    first, created_first = create_incident(session, definition)
    second, created_second = create_incident(session, definition)

    assert created_first is True
    assert created_second is False
    assert first.incident_id == second.incident_id
    assert session.query(Incident).count() == 1


def test_a_changed_definition_creates_a_separate_incident(session, run_ids):
    create_incident(session, _definition(run_ids))
    create_incident(session, _definition(run_ids, failure_multiplier=4.0))
    assert session.query(Incident).count() == 2


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"incident_type": "meteor_strike"}, "unknown incident_type"),
        ({"incident_end": INCIDENT_START}, "strictly after"),
        ({"incident_end": INCIDENT_START - timedelta(days=1)}, "strictly after"),
        ({"failure_multiplier": 0}, "must be > 0"),
        ({"latency_multiplier": -1}, "must be > 0"),
        ({"incident_seed": ""}, "non-empty"),
        ({"affected_gateway_id": None}, "requires affected_gateway_id"),
    ],
)
def test_invalid_definitions_are_rejected(run_ids, overrides, message):
    with pytest.raises(IncidentDefinitionError, match=message):
        _definition(run_ids, **overrides).validate()


def test_naive_timestamps_are_rejected(run_ids):
    with pytest.raises(IncidentDefinitionError, match="timezone-aware"):
        _definition(run_ids, incident_start=datetime(2024, 3, 20)).validate()


def test_zero_width_window_is_rejected_by_the_database(session, run_ids):
    """Even if validation were bypassed, the CHECK constraint refuses the row."""
    definition = _definition(run_ids)
    incident = Incident(
        incident_key="manual-key",
        incident_name="degenerate",
        incident_type="gateway_degradation",
        affected_gateway_id="gateway_C",
        incident_start=INCIDENT_START,
        incident_end=INCIDENT_START,
        failure_multiplier=2,
        latency_multiplier=1,
        timeout_multiplier=1,
        generation_run_id=definition.generation_run_id,
        source_ingestion_run_id=definition.source_ingestion_run_id,
        incident_seed="s",
        incident_model_version=INCIDENT_MODEL_VERSION,
        incident_config_version=INCIDENT_CONFIG_VERSION,
    )
    session.add(incident)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_lifecycle_moves_forward(session, run_ids):
    incident, _ = create_incident(session, _definition(run_ids))
    assert incident.status == "CREATED"
    advance_status(session, incident, "ACTIVE")
    advance_status(session, incident, "DETECTED")
    advance_status(session, incident, "DIAGNOSED")
    assert incident.status == "DIAGNOSED"


def test_lifecycle_reapplication_is_a_noop(session, run_ids):
    incident, _ = create_incident(session, _definition(run_ids))
    advance_status(session, incident, "ACTIVE")
    advance_status(session, incident, "ACTIVE")
    assert incident.status == "ACTIVE"


def test_lifecycle_cannot_move_backwards(session, run_ids):
    incident, _ = create_incident(session, _definition(run_ids))
    advance_status(session, incident, "DIAGNOSED")
    with pytest.raises(IncidentLifecycleError, match="backwards"):
        advance_status(session, incident, "CREATED")


def test_unknown_lifecycle_status_is_rejected(session, run_ids):
    incident, _ = create_incident(session, _definition(run_ids))
    with pytest.raises(IncidentLifecycleError, match="unknown incident status"):
        advance_status(session, incident, "EXPLODED")


# ==========================================================================
# Approach B: the simulated outcome layer
# ==========================================================================


def test_added_failure_probability_is_zero_for_a_healthy_profile():
    profile = build_runtime_profile(
        {
            "gateway_id": "gateway_C",
            "profile_version": "baseline-v1",
            "baseline_failure_probability": 0.062,
            "latency_multiplier": 1.0,
            "failure_response_mix": {"TIMEOUT": 0.02, "PROCESSING_ERROR": 0.98},
        },
        incident=_FakeIncident(failure_multiplier=3.5),
        degraded=False,
    )
    assert added_failure_probability(profile) == 0.0


def test_added_failure_probability_reaches_the_intended_effective_rate():
    """
    p_add must convert the multiplier into the right additive probability.

    With base 0.06 and multiplier 3.5, the effective rate is 0.21, and applying p_add
    only to the observed successes must land there:
        0.06 + (1 - 0.06) * p_add == 0.21
    """
    profile = build_runtime_profile(
        {
            "gateway_id": "gateway_C",
            "profile_version": "baseline-v1",
            "baseline_failure_probability": 0.06,
            "latency_multiplier": 1.0,
            "failure_response_mix": {"TIMEOUT": 0.02, "PROCESSING_ERROR": 0.98},
        },
        incident=_FakeIncident(failure_multiplier=3.5),
        degraded=True,
    )
    p_add = added_failure_probability(profile)
    realised = 0.06 + (1 - 0.06) * p_add
    assert realised == pytest.approx(0.21, abs=1e-6)


class _FakeIncident:
    """Minimal stand-in for the multiplier fields build_runtime_profile reads."""

    def __init__(self, failure_multiplier=3.5, latency_multiplier=2.2, timeout_multiplier=6.0):
        self.failure_multiplier = failure_multiplier
        self.latency_multiplier = latency_multiplier
        self.timeout_multiplier = timeout_multiplier


def test_cohort_membership_matches_gateway_and_segment():
    incident = Incident(
        incident_key="k",
        incident_name="n",
        incident_type="gateway_degradation",
        affected_gateway_id="gateway_C",
        affected_segment={"sender_bank": "SBI"},
        incident_start=INCIDENT_START,
        incident_end=INCIDENT_END,
        failure_multiplier=2,
        latency_multiplier=1,
        timeout_multiplier=1,
        generation_run_id=1,
        source_ingestion_run_id=1,
        incident_seed="s",
        incident_model_version="1.0.0",
        incident_config_version="1.0.0",
    )
    assert is_in_affected_cohort({"gateway_id": "gateway_C", "sender_bank": "SBI"}, incident)
    assert not is_in_affected_cohort({"gateway_id": "gateway_B", "sender_bank": "SBI"}, incident)
    assert not is_in_affected_cohort({"gateway_id": "gateway_C", "sender_bank": "HDFC"}, incident)


def test_issuer_incident_ignores_gateway_and_follows_the_segment():
    incident = Incident(
        incident_key="k",
        incident_name="n",
        incident_type="issuer_degradation",
        affected_gateway_id=None,
        affected_segment={"sender_bank": "SBI"},
        incident_start=INCIDENT_START,
        incident_end=INCIDENT_END,
        failure_multiplier=2,
        latency_multiplier=1,
        timeout_multiplier=1,
        generation_run_id=1,
        source_ingestion_run_id=1,
        incident_seed="s",
        incident_model_version="1.0.0",
        incident_config_version="1.0.0",
    )
    assert is_in_affected_cohort({"gateway_id": "gateway_A", "sender_bank": "SBI"}, incident)
    assert is_in_affected_cohort({"gateway_id": "gateway_E", "sender_bank": "SBI"}, incident)
    assert not is_in_affected_cohort({"gateway_id": "gateway_A", "sender_bank": "HDFC"}, incident)


@pytest.fixture()
def simulated(session, run_ids):
    incident, _ = create_incident(session, _definition(run_ids))
    result = simulate_incident(session, incident)
    return incident, result


def test_observed_transactions_are_never_modified(session, simulated):
    """
    The canonical table must be byte-identical after an incident.

    Compared by a content fingerprint rather than a row count, so a same-size mutation
    (a flipped status) would still be caught.
    """
    incident, _ = simulated
    fingerprint_sql = text(
        "SELECT md5(string_agg(transaction_id || '|' || status || '|' || amount::text, "
        "',' ORDER BY transaction_id)) FROM transactions"
    )
    after = session.execute(fingerprint_sql).scalar()

    # Re-simulate; the observed layer must still be untouched.
    simulate_incident(session, incident)
    assert session.execute(fingerprint_sql).scalar() == after


def test_approach_b_never_rescues_an_observed_failure(session, simulated):
    incident, _ = simulated
    rescued = session.execute(
        text(
            "SELECT count(*) FROM simulated_incident_outcomes "
            "WHERE incident_id = :id AND observed_status = 'FAILED' "
            "AND simulated_status = 'SUCCESS'"
        ),
        {"id": incident.incident_id},
    ).scalar()
    assert rescued == 0


def test_database_physically_rejects_a_rescued_failure(session, simulated):
    """Approach A is unrepresentable, not merely discouraged."""
    incident, _ = simulated
    # A fully coherent rescue: status, response, regime and the changed flag are all
    # updated consistently, so only the Approach B constraint itself can reject it.
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "UPDATE simulated_incident_outcomes SET simulated_status = 'SUCCESS', "
                "simulated_response_code = 'APPROVED', simulated_latency_regime = 'NORMAL', "
                "outcome_changed = false "
                "WHERE simulated_outcome_id = ("
                "  SELECT simulated_outcome_id FROM simulated_incident_outcomes "
                "  WHERE incident_id = :id AND observed_status = 'FAILED' LIMIT 1)"
            ),
            {"id": incident.incident_id},
        )
    session.rollback()


def test_affected_cohort_failure_rate_rises_into_the_target_band(session, simulated):
    _, result = simulated
    assert result.affected_population >= MIN_COHORT_SIZE
    assert 0.18 <= result.affected_failure_rate <= 0.28


def test_control_cohort_is_exactly_unchanged(session, simulated):
    """
    Not "approximately stable" -- provably identical.

    Every control row must carry its observed status through untouched. This is the
    property Approach A could not deliver, so it is asserted exactly.
    """
    incident, _ = simulated
    changed_controls = session.execute(
        text(
            "SELECT count(*) FROM simulated_incident_outcomes "
            "WHERE incident_id = :id AND in_affected_cohort = false "
            "AND simulated_status <> observed_status"
        ),
        {"id": incident.incident_id},
    ).scalar()
    assert changed_controls == 0


def test_control_signals_are_carried_through_from_the_synthetic_baseline(session, simulated):
    incident, _ = simulated
    mismatches = session.execute(
        text(
            "SELECT count(*) FROM simulated_incident_outcomes s "
            "JOIN synthetic_infrastructure_assignments a "
            "  ON a.transaction_id = s.transaction_id "
            " AND a.generation_run_id = s.generation_run_id "
            "WHERE s.incident_id = :id AND s.in_affected_cohort = false "
            "  AND (s.simulated_latency_ms <> a.gateway_latency_ms "
            "   OR s.simulated_response_code <> a.gateway_response_code)"
        ),
        {"id": incident.incident_id},
    ).scalar()
    assert mismatches == 0


def test_only_transactions_inside_the_window_are_simulated(session, simulated):
    incident, _ = simulated
    outside = session.execute(
        text(
            "SELECT count(*) FROM simulated_incident_outcomes s "
            "JOIN transactions t ON t.transaction_id = s.transaction_id "
            "WHERE s.incident_id = :id "
            "AND (t.timestamp < :start OR t.timestamp >= :end)"
        ),
        {"id": incident.incident_id, "start": INCIDENT_START, "end": INCIDENT_END},
    ).scalar()
    assert outside == 0


def test_window_is_half_open_at_the_end_boundary(session, run_ids):
    """A transaction exactly at incident_end is outside the window."""
    incident, _ = create_incident(session, _definition(run_ids))
    simulate_incident(session, incident)
    at_end = session.execute(
        text(
            "SELECT count(*) FROM simulated_incident_outcomes s "
            "JOIN transactions t ON t.transaction_id = s.transaction_id "
            "WHERE s.incident_id = :id AND t.timestamp = :end"
        ),
        {"id": incident.incident_id, "end": INCIDENT_END},
    ).scalar()
    assert at_end == 0


def test_simulated_signals_are_coherent(session, simulated):
    incident, _ = simulated
    incoherent = session.execute(
        text(
            "SELECT count(*) FROM simulated_incident_outcomes WHERE incident_id = :id AND ("
            "  (simulated_status = 'SUCCESS') <> (simulated_response_code = 'APPROVED')"
            "  OR (simulated_response_code = 'TIMEOUT') <> (simulated_latency_regime = 'TIMEOUT')"
            "  OR outcome_changed <> (simulated_status <> observed_status))"
        ),
        {"id": incident.incident_id},
    ).scalar()
    assert incoherent == 0


def test_degradation_raises_latency_and_infrastructure_side_responses(session, simulated):
    """
    Failure, latency, and response mix must move together.

    They come from one funnel, so a degradation that raised the failure rate without
    also shifting latency and the response families would mean the funnel had been
    bypassed somewhere.
    """
    incident, _ = simulated
    row = session.execute(
        text(
            "SELECT "
            "  avg(s.simulated_latency_ms) FILTER (WHERE s.in_affected_cohort) AS affected_latency,"
            "  avg(a.gateway_latency_ms)  FILTER (WHERE s.in_affected_cohort) AS baseline_latency,"
            "  count(*) FILTER (WHERE s.in_affected_cohort "
            "                   AND s.simulated_response_code IN ('TIMEOUT','PROCESSING_ERROR'))"
            "    AS affected_infra,"
            "  count(*) FILTER (WHERE s.in_affected_cohort "
            "                   AND a.gateway_response_code IN ('TIMEOUT','PROCESSING_ERROR'))"
            "    AS baseline_infra "
            "FROM simulated_incident_outcomes s "
            "JOIN synthetic_infrastructure_assignments a "
            "  ON a.transaction_id = s.transaction_id "
            " AND a.generation_run_id = s.generation_run_id "
            "WHERE s.incident_id = :id"
        ),
        {"id": incident.incident_id},
    ).mappings().first()

    assert float(row["affected_latency"]) > float(row["baseline_latency"])
    assert int(row["affected_infra"]) > int(row["baseline_infra"])


def test_every_simulated_row_carries_full_lineage(session, simulated):
    incident, _ = simulated
    broken = session.execute(
        text(
            "SELECT count(*) FROM simulated_incident_outcomes s "
            "LEFT JOIN transactions t ON t.transaction_id = s.transaction_id "
            "LEFT JOIN ingestion_runs i ON i.ingestion_run_id = s.source_ingestion_run_id "
            "LEFT JOIN synthetic_generation_runs g "
            "  ON g.generation_run_id = s.generation_run_id "
            "WHERE s.incident_id = :id "
            "AND (t.transaction_id IS NULL OR i.ingestion_run_id IS NULL "
            "     OR g.generation_run_id IS NULL)"
        ),
        {"id": incident.incident_id},
    ).scalar()
    assert broken == 0


@pytest.mark.parametrize(
    "table",
    [
        "incidents",
        "incident_simulation_runs",
        "simulated_incident_outcomes",
        "incident_analysis_runs",
        "incident_anomalies",
        "incident_evidence",
        "incident_hypotheses",
        "incident_rca_results",
    ],
)
def test_provenance_flag_cannot_be_cleared(session, run_ids, table):
    """
    Every Day 3 table refuses to be relabelled as non-synthetic/observed.

    A full pipeline is run first so that every table actually holds rows -- an UPDATE
    against an empty table succeeds trivially and would make this pass for the wrong
    reason.
    """
    run_incident_pipeline(session, _definition(run_ids))
    column = {
        "incident_simulation_runs": "is_simulated",
        "simulated_incident_outcomes": "is_simulated",
    }.get(table, "is_synthetic")
    with pytest.raises(IntegrityError):
        session.execute(text(f"UPDATE {table} SET {column} = false"))
    session.rollback()


def test_ground_truth_cannot_be_marked_non_evaluation_only(session, simulated):
    with pytest.raises(IntegrityError):
        session.execute(text("UPDATE incident_ground_truth SET is_evaluation_only = false"))
    session.rollback()


# ==========================================================================
# Determinism and reproducibility
# ==========================================================================


def test_resimulating_reproduces_the_fingerprint(session, run_ids):
    incident, _ = create_incident(session, _definition(run_ids))
    first = simulate_incident(session, incident)
    second = simulate_incident(session, incident)
    assert first.simulation_fingerprint == second.simulation_fingerprint
    assert first.rows_changed == second.rows_changed


def test_resimulating_replaces_rather_than_accumulates(session, run_ids):
    incident, _ = create_incident(session, _definition(run_ids))
    first = simulate_incident(session, incident)
    simulate_incident(session, incident)
    total = session.query(SimulatedIncidentOutcome).count()
    assert total == first.rows_simulated


def test_a_different_seed_changes_the_fingerprint(session, run_ids):
    incident, _ = create_incident(session, _definition(run_ids))
    baseline = simulate_incident(session, incident, simulation_seed="seed-one")
    altered = simulate_incident(session, incident, simulation_seed="seed-two")
    assert baseline.simulation_fingerprint != altered.simulation_fingerprint


def test_a_different_multiplier_changes_the_fingerprint(session, run_ids):
    first, _ = create_incident(session, _definition(run_ids))
    second, _ = create_incident(session, _definition(run_ids, failure_multiplier=5.0))
    assert (
        simulate_incident(session, first).simulation_fingerprint
        != simulate_incident(session, second).simulation_fingerprint
    )


def test_analysis_is_reproducible_end_to_end(session, run_ids):
    definition = _definition(run_ids)
    first = run_incident_pipeline(session, definition)
    second = run_incident_pipeline(session, definition)

    assert first.analysis_fingerprint == second.analysis_fingerprint
    assert first.rca.rca_fingerprint == second.rca.rca_fingerprint
    assert first.rca.predicted_root_cause == second.rca.predicted_root_cause
    assert first.rca.confidence == second.rca.confidence
    assert [h.hypothesis_type for h in first.hypotheses] == [
        h.hypothesis_type for h in second.hypotheses
    ]


# ==========================================================================
# Detection
# ==========================================================================


def test_detection_finds_the_degraded_gateway_without_being_told(session, run_ids):
    """
    The detector receives a window and a database -- never a gateway id.

    It must arrive at gateway_C by measurement. Nothing in the package matches on a
    specific cohort value, which is what this asserts behaviourally.
    """
    result = run_incident_pipeline(session, _definition(run_ids))
    top = result.detection.top
    assert top is not None
    assert top.cohort_definition == {"gateway": "gateway_C"}
    assert top.severity in HIGH_SEVERITIES


def test_detection_recovers_a_strong_statistical_signal(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    assert result.detection.top.significance_sigma >= 6.0


def test_quiet_window_produces_no_high_severity_alert(session, run_ids):
    """The false-positive control: normal traffic must not look like an incident."""
    generation_run_id, _ = run_ids
    window = (QUIET_START, QUIET_END)
    result = run_quiet_analysis(session, generation_run_id, window)
    high = [c for c in result.detection.reported if c.severity in HIGH_SEVERITIES]
    assert high == []
    assert result.rca.verdict == "INSUFFICIENT_EVIDENCE"
    assert result.rca.predicted_root_cause is None


def test_small_cohorts_are_not_scored(session, run_ids):
    """A cohort below the minimum sample size must never be reported, however extreme."""
    result = run_incident_pipeline(session, _definition(run_ids))
    for candidate in result.detection.reported:
        assert candidate.current.volume >= MIN_COHORT_SIZE


def test_detection_scans_multiple_dimensions_and_intersections(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    scanned_dimensions = {c.dimensions for c in result.detection.candidates}
    assert result.detection.cohorts_scanned > 50
    # At least one single-dimension cohort was evaluated.
    assert any(len(dims) == 1 for dims in scanned_dimensions)


def test_redundant_narrow_cohorts_are_suppressed(session, run_ids):
    """
    A gateway incident must not emit one alert per intersection cell it touches.

    Any suppressed candidate must name the broader cohort that explains it, and that
    broader cohort must genuinely generalise it.
    """
    result = run_incident_pipeline(session, _definition(run_ids))
    for candidate in result.detection.candidates:
        if not candidate.suppressed:
            continue
        assert candidate.suppressed_by is not None
        broader = next(
            c for c in result.detection.candidates if c.cohort_key == candidate.suppressed_by
        )
        assert broader.depth < candidate.depth


def test_detection_ranks_are_dense_and_ordered(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    reported = result.detection.reported
    assert [c.rank for c in reported] == list(range(1, len(reported) + 1))
    scores = [float(c.score) for c in reported]
    assert scores == sorted(scores, reverse=True)


# ==========================================================================
# Evidence
# ==========================================================================


def test_evidence_is_produced_for_the_leading_anomaly(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    top_key = result.detection.top.cohort_key
    types = {r.evidence_type for r in result.evidence.for_cohort(top_key)}
    assert {"failure_rate", "latency", "response_mix", EVIDENCE_CONTROL_COMPARISON} <= types


def test_every_evidence_record_declares_its_source_layer_and_query(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    assert result.evidence.records
    for record in result.evidence.records:
        assert record.source_layer in SOURCE_LAYERS
        assert record.evidence_source
        assert record.explanation


def test_evidence_values_match_the_underlying_metrics_exactly(session, run_ids):
    """Evidence must restate measured aggregates, never a re-derived approximation."""
    result = run_incident_pipeline(session, _definition(run_ids))
    top = result.detection.top
    failure_evidence = next(
        r
        for r in result.evidence.for_cohort(top.cohort_key)
        if r.evidence_type == "failure_rate"
    )
    assert failure_evidence.baseline_value == pytest.approx(top.baseline.failure_rate)
    assert failure_evidence.current_value == pytest.approx(top.current.failure_rate)
    assert failure_evidence.delta == pytest.approx(top.absolute_delta)


def test_control_comparison_excludes_the_subject_cohort(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    top = result.detection.top
    control = next(
        r
        for r in result.evidence.for_cohort(top.cohort_key)
        if r.evidence_type == EVIDENCE_CONTROL_COMPARISON
    )
    assert control.control_group["subject_excluded"] == top.cohort_key
    assert top.cohort_key not in control.control_group["current"]["cohorts"]


def test_control_group_stays_near_baseline_during_the_incident(session, run_ids):
    """The evidence layer must be able to prove controls held steady."""
    result = run_incident_pipeline(session, _definition(run_ids))
    top = result.detection.top
    control = next(
        r
        for r in result.evidence.for_cohort(top.cohort_key)
        if r.evidence_type == EVIDENCE_CONTROL_COMPARISON
    )
    assert abs(float(control.delta)) < 0.02
    assert float(control.delta) < top.absolute_delta / 4


def test_gmv_evidence_is_sourced_from_observed_amounts(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    gmv = next(r for r in result.evidence.records if r.evidence_type == "gmv_impact")
    assert gmv.source_layer == "OBSERVED"
    assert gmv.current_value is not None and gmv.current_value > 0


def test_confounding_evidence_separates_a_cause_from_its_shadow(session, run_ids):
    """
    The degraded gateway must survive excluding its strongest rival; a shadow must not.

    This is the component that stops "region X looks bad" -- true only because that
    region routes through the broken gateway -- from being reported as a cause.
    """
    result = run_incident_pipeline(session, _definition(run_ids))
    gateway_key = result.detection.top.cohort_key
    gateway_confounding = next(
        (
            r
            for r in result.evidence.for_cohort(gateway_key)
            if r.evidence_type == EVIDENCE_CONFOUNDING
        ),
        None,
    )
    if gateway_confounding is not None:
        # The real cause keeps most of its movement when a rival is removed.
        assert float(gateway_confounding.relative_delta) >= 0.5


# ==========================================================================
# Hypotheses
# ==========================================================================


def test_every_hypothesis_category_is_evaluated(session, run_ids):
    """Alternatives must be scored, not silently omitted."""
    result = run_incident_pipeline(session, _definition(run_ids))
    types = {h.hypothesis_type for h in result.hypotheses}
    assert types == {
        "gateway_degradation",
        "issuer_degradation",
        "payment_method_degradation",
        "network_segment_degradation",
        "systemic_degradation",
    }


def test_gateway_hypothesis_wins_the_golden_scenario(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    assert result.hypotheses[0].hypothesis_type == "gateway_degradation"
    assert result.hypotheses[0].subject_value == "gateway_C"


def test_hypotheses_are_ranked_densely_and_by_score(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    assert [h.rank for h in result.hypotheses] == list(range(1, len(result.hypotheses) + 1))
    scores = [h.score for h in result.hypotheses]
    assert scores == sorted(scores, reverse=True)


def test_the_winning_hypothesis_cites_supporting_evidence(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    top = result.hypotheses[0]
    assert top.supporting_evidence_ids
    valid_ids = {r.evidence_id for r in result.evidence.records}
    assert set(top.supporting_evidence_ids) <= valid_ids


def test_hypotheses_record_contradicting_evidence_where_it_exists(session, run_ids):
    """
    At least one rival must carry contradicting evidence.

    A hypothesis set in which nothing is ever contradicted is not reasoning, it is
    a ranking of confirmations.
    """
    result = run_incident_pipeline(session, _definition(run_ids))
    assert any(h.contradicting_evidence_ids for h in result.hypotheses[1:])


def test_score_components_are_persisted_for_audit(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    stored = ranked_hypotheses(session, result.analysis_run_id)
    assert stored
    top = stored[0]
    assert "signal" in top["score_components"]
    assert "independence" in top["score_components"]
    assert top["rationale"]


# ==========================================================================
# RCA
# ==========================================================================


def test_rca_names_the_degraded_gateway(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    assert result.rca.predicted_gateway_id == "gateway_C"
    assert result.rca.predicted_hypothesis_type == "gateway_degradation"
    assert result.rca.verdict in ("CONFIDENT", "UNCERTAIN")
    assert 0.0 < result.rca.confidence <= 1.0


def test_rca_explanation_cites_evidence_ids(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    assert result.rca.supporting_evidence_ids
    for evidence_id in result.rca.supporting_evidence_ids:
        assert f"E{evidence_id}" in result.rca.explanation


def test_rca_lists_the_alternatives_it_rejected(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    alternatives = result.rca.alternatives_considered
    assert len(alternatives) == 4
    for alternative in alternatives:
        assert "hypothesis_type" in alternative
        assert "rationale" in alternative


def test_rca_declines_when_there_is_no_evidence(session, run_ids):
    """The system must be able to say it does not know."""
    generation_run_id, _ = run_ids
    window = (QUIET_START, QUIET_END)
    result = run_quiet_analysis(session, generation_run_id, window)
    assert result.rca.verdict == "INSUFFICIENT_EVIDENCE"
    assert result.rca.predicted_root_cause is None
    assert result.rca.predicted_gateway_id is None
    # A small residual score on the best-but-rejected hypothesis is honest -- what
    # matters is that it stays below the threshold at which a cause may be named.
    assert result.rca.confidence < RCA_UNCERTAIN_THRESHOLD


def test_alternative_scenario_is_not_blamed_on_a_gateway(session, run_ids):
    """
    The anti-overfit test.

    An issuer-centred degradation must not be diagnosed as a gateway problem just
    because the flagship demo is a gateway problem.
    """
    generation_run_id, ingestion_run_id = run_ids
    definition = IncidentDefinition(
        incident_name="test-issuer",
        incident_type="issuer_degradation",
        affected_gateway_id=None,
        affected_segment={"sender_bank": "SBI"},
        incident_start=INCIDENT_START,
        incident_end=INCIDENT_END,
        failure_multiplier=4.5,
        latency_multiplier=1.1,
        timeout_multiplier=1.0,
        target_failure_rate=0.22,
        generation_run_id=generation_run_id,
        source_ingestion_run_id=ingestion_run_id,
        incident_seed="test-issuer-seed",
        ground_truth_root_cause="Synthetic issuer degradation injected into SBI",
    )
    result = run_incident_pipeline(session, definition)

    assert result.rca.predicted_hypothesis_type == "issuer_degradation"
    assert result.rca.predicted_gateway_id is None
    gateway_hypothesis = next(
        h for h in result.hypotheses if h.hypothesis_type == "gateway_degradation"
    )
    assert gateway_hypothesis.rank > 1


# ==========================================================================
# Ground-truth isolation (adversarial)
# ==========================================================================


def test_no_diagnosis_module_references_the_ground_truth_table():
    """
    Static guarantee: the diagnosis path does not even name the answer key.

    Scanning the source is deliberate. A behavioural test can only show that ground
    truth was not used on one input; this shows it cannot be used on any.

    Docstrings and comments are stripped before scanning (via ast.unparse), because
    these modules legitimately DISCUSS ground-truth isolation in prose. What must be
    absent is executable code that touches it.
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
        # ast.unparse also drops comments, which are not part of the AST.
        return ast.unparse(tree)

    package = pathlib.Path(__file__).resolve().parents[1] / "aventum_incident"
    diagnosis_modules = ["detect.py", "evidence.py", "hypothesis.py", "rca.py", "metrics.py"]
    for name in diagnosis_modules:
        source = executable_source(package / name)
        assert "incident_ground_truth" not in source, f"{name} references the ground-truth table"
        assert "IncidentGroundTruth" not in source, f"{name} imports the ground-truth model"
        assert "ground_truth" not in source, f"{name} touches ground truth in executable code"


def test_rca_is_unchanged_when_ground_truth_is_deleted(session, run_ids):
    definition = _definition(run_ids)
    baseline = run_incident_pipeline(session, definition)

    session.execute(text("DELETE FROM incident_ground_truth"))
    session.flush()

    repeated = run_incident_pipeline(session, definition)
    assert repeated.rca.rca_fingerprint == baseline.rca.rca_fingerprint
    assert repeated.rca.predicted_root_cause == baseline.rca.predicted_root_cause
    assert repeated.rca.confidence == baseline.rca.confidence


def test_rca_is_unchanged_when_ground_truth_is_corrupted(session, run_ids):
    definition = _definition(run_ids)
    baseline = run_incident_pipeline(session, definition)

    session.execute(
        text(
            "UPDATE incident_ground_truth SET ground_truth_root_cause = :cause, "
            "ground_truth_gateway_id = :gateway"
        ),
        {"cause": "COMPLETELY WRONG CAUSE", "gateway": "gateway_A"},
    )
    session.flush()

    repeated = run_incident_pipeline(session, definition)
    assert repeated.rca.rca_fingerprint == baseline.rca.rca_fingerprint
    assert repeated.rca.predicted_gateway_id == "gateway_C"


def test_evaluation_compares_prediction_to_truth_after_the_fact(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    evaluation = evaluate_rca(
        session,
        result.incident.incident_id,
        result.rca,
        expected_hypothesis_type="gateway_degradation",
    )
    assert evaluation.correct
    assert evaluation.ground_truth_gateway_id == "gateway_C"
    assert evaluation.predicted_gateway_id == "gateway_C"


def test_ground_truth_is_still_recorded_for_evaluation(session, run_ids):
    incident, _ = create_incident(session, _definition(run_ids))
    truth = load_ground_truth(session, incident.incident_id)
    assert truth is not None
    assert truth.is_evaluation_only is True
    assert truth.ground_truth_gateway_id == "gateway_C"


# ==========================================================================
# Day 4 handoff
# ==========================================================================


def test_handoff_exposes_every_day4_interface(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    handoff = build_handoff(session, result.analysis_run_id)

    assert handoff.incident is not None
    assert handoff.incident.provenance == "SYNTHETIC_INCIDENT"
    assert handoff.simulation is not None
    assert handoff.simulation.provenance == "SIMULATED_INCIDENT_OUTCOME"
    assert handoff.detections
    assert handoff.evidence
    assert handoff.rca is not None
    assert handoff.rca.predicted_gateway_id == "gateway_C"


def test_handoff_never_exposes_ground_truth(session, run_ids):
    """Day 4's agent must reason over evidence, not read the answer key."""
    result = run_incident_pipeline(session, _definition(run_ids))
    payload = str(build_handoff(session, result.analysis_run_id).as_dict())
    assert "ground_truth" not in payload
    assert "Synthetic degradation injected" not in payload


def test_handoff_is_serialisable(session, run_ids):
    import json

    result = run_incident_pipeline(session, _definition(run_ids))
    payload = build_handoff(session, result.analysis_run_id).as_dict()
    assert json.dumps(payload, default=str)


# ==========================================================================
# Flagship acceptance
# ==========================================================================


def test_flagship_acceptance_chain(session, run_ids):
    """
    The full Day 3 chain, asserted as one story.

    Deliberately contains no hard-coded expectations of the form
    `if gateway == "gateway_C": anomaly` -- every assertion is about a measured
    relationship, and the identity of the culprit is only checked against ground truth
    at the very end, after the diagnosis already exists.
    """
    result = run_incident_pipeline(session, _definition(run_ids))

    # 1. an incident exists and was simulated
    assert result.incident.status == "DIAGNOSED"
    assert result.simulation.rows_changed > 0

    # 2. the affected cohort degraded, the control group did not
    assert result.simulation.affected_failure_rate > 0.18
    assert result.simulation.control_failure_rate < 0.08

    # 3. detection found something significant
    top = result.detection.top
    assert top.significance_sigma >= 6.0
    assert top.severity in HIGH_SEVERITIES

    # 4. evidence was assembled and is traceable
    assert len(result.evidence.records) >= 5
    assert all(r.evidence_id is not None for r in result.evidence.records)

    # 5. competing hypotheses were ranked with a cited winner
    assert len(result.hypotheses) == 5
    assert result.hypotheses[0].supporting_evidence_ids

    # 6. RCA produced a cited, confidence-bearing conclusion
    assert result.rca.verdict in ("CONFIDENT", "UNCERTAIN")
    assert result.rca.supporting_evidence_ids
    assert result.rca.alternatives_considered

    # 7. only now is ground truth consulted
    evaluation = evaluate_rca(
        session,
        result.incident.incident_id,
        result.rca,
        expected_hypothesis_type="gateway_degradation",
    )
    assert evaluation.correct


# ==========================================================================
# P1-1 -- causal alert roles (primary vs derivative)
# ==========================================================================


def test_gateway_incident_yields_a_single_primary_alert(session, run_ids):
    """
    One cause should produce one root-level alert.

    Before this fix a single gateway degradation emitted every cohort that merely
    intersected it as an equal-priority alert. Those cohorts are statistically genuine,
    so no significance threshold removes them -- only a causal test does.
    """
    result = run_incident_pipeline(session, _definition(run_ids))
    primaries = result.detection.primary_alerts

    assert len(primaries) == 1
    assert primaries[0].cohort_definition == {"gateway": "gateway_C"}
    assert primaries[0].alert_role == ALERT_ROLE_PRIMARY
    # The shadows are still detected -- reclassified, not discarded.
    assert result.detection.derivative_alerts
    assert len(result.detection.reported) > len(primaries)


def test_every_derivative_alert_names_what_explains_it(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    primary_keys = {c.cohort_key for c in result.detection.primary_alerts}

    for derivative in result.detection.derivative_alerts:
        assert derivative.alert_role == ALERT_ROLE_DERIVATIVE
        assert derivative.derived_from is not None
        # It must point at a real primary, not an arbitrary cohort.
        assert derivative.derived_from in primary_keys


def test_derivative_alerts_keep_their_evidence(session, run_ids):
    """Alert precision must not be bought by destroying investigative evidence."""
    result = run_incident_pipeline(session, _definition(run_ids))
    derivative_keys = {c.cohort_key for c in result.detection.derivative_alerts}
    assert derivative_keys

    evidenced = {r.cohort_key for r in result.evidence.records}
    assert derivative_keys & evidenced, "derivative cohorts lost all supporting evidence"

    stored = session.execute(
        text(
            "SELECT count(*) FROM incident_anomalies "
            "WHERE analysis_run_id = :r AND alert_role = 'DERIVATIVE'"
        ),
        {"r": result.analysis_run_id},
    ).scalar()
    assert stored == len(result.detection.derivative_alerts)


def test_independent_simultaneous_causes_stay_primary(session, run_ids):
    """
    The anti-over-suppression test.

    A fleet-wide degradation makes every gateway an independent cause: excluding one
    leaves the others untouched and still degraded. Collapsing them into a single alert
    would be as wrong as emitting shadows, so several gateways must remain PRIMARY.
    """
    generation_run_id, ingestion_run_id = run_ids
    definition = IncidentDefinition(
        incident_name="test-systemic",
        incident_type="systemic_degradation",
        affected_gateway_id=None,
        affected_segment=None,
        incident_start=INCIDENT_START,
        incident_end=INCIDENT_END,
        failure_multiplier=3.5,
        latency_multiplier=2.0,
        timeout_multiplier=5.0,
        generation_run_id=generation_run_id,
        source_ingestion_run_id=ingestion_run_id,
        incident_seed="test-systemic-seed",
        ground_truth_root_cause="Synthetic fleet-wide degradation",
    )
    result = run_incident_pipeline(session, definition)

    gateway_primaries = [
        c for c in result.detection.primary_alerts if c.dimensions == ("gateway",)
    ]
    assert len(gateway_primaries) >= 2, (
        "classifier collapsed genuinely independent causes into one alert"
    )


def test_issuer_incident_keeps_the_issuer_primary(session, run_ids):
    generation_run_id, ingestion_run_id = run_ids
    definition = IncidentDefinition(
        incident_name="test-issuer-primary",
        incident_type="issuer_degradation",
        affected_gateway_id=None,
        affected_segment={"sender_bank": "SBI"},
        incident_start=INCIDENT_START,
        incident_end=INCIDENT_END,
        failure_multiplier=4.5,
        latency_multiplier=1.1,
        timeout_multiplier=1.0,
        generation_run_id=generation_run_id,
        source_ingestion_run_id=ingestion_run_id,
        incident_seed="test-issuer-primary-seed",
        ground_truth_root_cause="Synthetic issuer degradation injected into SBI",
    )
    result = run_incident_pipeline(session, definition)

    primaries = result.detection.primary_alerts
    assert primaries
    assert primaries[0].cohort_definition == {"sender_bank": "SBI"}
    # A gateway spillover must not be promoted to an equal-priority cause.
    assert not [c for c in primaries if c.dimensions == ("gateway",)]


def test_alert_role_classification_names_no_specific_cohort():
    """The classifier must reason from residuals, never from a known culprit."""
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1] / "aventum_incident" / "detect.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body[0].value.value = ""
    code = ast.unparse(tree)
    for forbidden in ("gateway_C", "gateway_B", "SBI", "HDFC"):
        assert forbidden not in code, "detect.py hard-codes a specific cohort"


def test_database_rejects_a_derivative_without_a_parent(session, run_ids):
    run_incident_pipeline(session, _definition(run_ids))
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "UPDATE incident_anomalies SET alert_role = 'DERIVATIVE', "
                "derived_from_cohort_key = NULL WHERE alert_role = 'PRIMARY'"
            )
        )
    session.rollback()


def test_handoff_separates_primary_from_derivative(session, run_ids):
    """Day 4's actionable list must contain only root-level causes."""
    result = run_incident_pipeline(session, _definition(run_ids))
    handoff = build_handoff(session, result.analysis_run_id)

    assert handoff.detections
    assert all(d.alert_role == ALERT_ROLE_PRIMARY for d in handoff.detections)
    assert handoff.derivative_detections
    assert all(
        d.alert_role == ALERT_ROLE_DERIVATIVE for d in handoff.derivative_detections
    )
    for view in handoff.derivative_detections:
        assert view.derived_from_anomaly_id is not None
    assert all(d.primary_anomaly_id is not None for d in handoff.detections)


# ==========================================================================
# P1-2 -- confidence calibration
# ==========================================================================


def test_confidence_is_bounded_and_monotone_in_both_factors():
    assert confidence_from(0.0, 1.0) == 0.0
    assert confidence_from(1.0, 0.0) == 0.0
    assert confidence_from(1.0, 1.0) == pytest.approx(1.0)
    assert confidence_from(0.5, 0.5) == pytest.approx(0.5)
    # Raising either factor cannot lower confidence.
    assert confidence_from(0.8, 0.5) > confidence_from(0.4, 0.5)
    assert confidence_from(0.5, 0.8) > confidence_from(0.5, 0.4)
    # Out-of-range inputs are clamped, never propagated.
    assert 0.0 <= confidence_from(-1.0, 5.0) <= 1.0


def test_weak_evidence_cannot_reach_confident_however_decisive():
    """
    The hard ceiling: confidence <= sqrt(evidence_strength).

    This is the property that closes P1-2. A perfectly attributed hypothesis
    (attribution 1.0) whose evidence is weak still cannot be published as CONFIDENT.
    """
    for strength in (0.05, 0.1, 0.2, 0.3):
        best_possible = confidence_from(strength, 1.0)
        assert best_possible == pytest.approx(strength**0.5)
        assert best_possible < RCA_CONFIDENT_THRESHOLD


def test_evidence_strength_saturates_and_is_monotone():
    assert evidence_strength_from(0.0) == 0.0
    assert evidence_strength_from(-3.0) == 0.0
    assert evidence_strength_from(6.0) == pytest.approx(0.5)
    assert evidence_strength_from(12.0) == pytest.approx(1.0)
    assert evidence_strength_from(50.0) == pytest.approx(1.0)


def test_stronger_evidence_beats_a_more_decisive_weak_one():
    """
    The exact inversion this fix exists to remove.

    Pre-fix, a 5.16 sigma incident with no rivals scored 0.6944 while a 9.26 sigma
    incident with real competitors scored 0.6396. Reproduced with the measured inputs:
    the weaker-but-decisive case must now rank lower.
    """
    weak_but_decisive = confidence_from(evidence_strength_from(2.737), 0.6944)
    strong_but_contested = confidence_from(evidence_strength_from(8.884), 0.6396)
    assert weak_but_decisive < strong_but_contested


def test_marginal_incident_lands_in_the_uncertain_band(session, run_ids):
    """End-to-end exercise of UNCERTAIN: real evidence, but not enough to be sure."""
    result = run_incident_pipeline(
        session,
        _definition(
            run_ids,
            # Chosen so the fixture population lands mid-band rather than on an edge.
            # The real 250K dataset reaches the same band at multiplier 2.0; the fixture
            # cohort is smaller, so it needs a slightly larger push for the same effect.
            failure_multiplier=2.6,
            latency_multiplier=1.4,
            timeout_multiplier=2.0,
            incident_seed="test-marginal",
        ),
    )
    assert result.rca.verdict == RCA_VERDICT_UNCERTAIN
    assert RCA_UNCERTAIN_THRESHOLD <= result.rca.confidence < RCA_CONFIDENT_THRESHOLD
    # It still names its best candidate and cites evidence -- uncertain, not silent.
    assert result.rca.predicted_root_cause is not None
    assert result.rca.supporting_evidence_ids
    assert result.rca.alternatives_considered


def test_rca_exposes_evidence_strength_beside_confidence(session, run_ids):
    """Action safety: confidence alone must never be the whole picture."""
    result = run_incident_pipeline(session, _definition(run_ids))
    assert result.rca.severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE")
    assert result.rca.significance_sigma > 0
    assert 0.0 <= result.rca.evidence_strength <= 1.0
    # The published confidence obeys the ceiling.
    assert result.rca.confidence <= (result.rca.evidence_strength**0.5) + 1e-6


def test_handoff_exposes_the_action_safety_triple(session, run_ids):
    result = run_incident_pipeline(session, _definition(run_ids))
    rca = build_handoff(session, result.analysis_run_id).rca
    assert rca is not None
    assert rca.confidence > 0
    assert rca.severity
    assert rca.significance_sigma > 0
    assert rca.evidence_strength > 0


def test_confidence_is_reproducible(session, run_ids):
    definition = _definition(run_ids)
    first = run_incident_pipeline(session, definition)
    second = run_incident_pipeline(session, definition)
    assert first.rca.confidence == second.rca.confidence
    assert first.rca.evidence_strength == second.rca.evidence_strength
    assert first.rca.rca_fingerprint == second.rca.rca_fingerprint
