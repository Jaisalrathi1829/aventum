"""
Day 5 tests: independent verification, batch recovery measurement, demo reset.

These test the JUDGEMENT, not just the plumbing. The most important cases here are the
ones where verification returns something other than success: a verifier that cannot
disagree with the action it is grading is a formality, and several tests below exist
specifically to prove it can.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from aventum_action.models import Action
from aventum_action.pipeline import run_full_flow
from aventum_api.demo import PROTECTED_TABLES, WORKFLOW_TABLES, reset_demo_state
from aventum_incident.pipeline import run_incident_pipeline
from aventum_ingest.pipeline import run_ingestion
from aventum_synth.generator import run_generation
from aventum_verification.batch import build_batch_summary
from aventum_verification.constants import (
    ATTAINMENT_EFFECTIVE,
    ATTAINMENT_FLOOR,
    MIN_MEANINGFUL_FAILURE_RATE_IMPROVEMENT,
    PARTIALLY_EFFECTIVE,
    RECOVERY_EFFECTIVE,
    RECOVERY_NOT_VERIFIED,
    VERIFICATION_COMPLETE,
    VERIFICATION_INELIGIBLE,
    VERIFICATION_MODEL_VERSION,
)
from aventum_verification.models import Verification
from aventum_verification.verify import _classify, get_verification, verify_action

# The same fixture population the Day 3 and Day 4 suites use, so production thresholds
# apply unchanged and Day 5 is measured against the incident the rest of the system was
# built and tested against.
from tests.test_incident_intelligence import _definition, _fixture_rows

APPROVER = "test.approver@aventum.test"


@pytest.fixture()
def day5_engine(engine, registered_source):
    # The SAME generation seed as the Day 4 suite, deliberately. The seed determines the
    # synthetic infrastructure, which determines whether the incident is strong enough to
    # warrant acting at all -- a different seed produced a NO_ACTION world and nothing to
    # verify. Day 5 must be measured against the same flagship the rest of the system was
    # built against, not a differently-shaped one.
    run_ingestion(engine, registered_source(_fixture_rows(), name="day5-fixture.csv"))
    result = run_generation(engine, generation_seed="day4-test-seed")
    assert result.succeeded
    return engine


@pytest.fixture()
def session(day5_engine) -> Session:
    with Session(day5_engine) as sess:
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


@pytest.fixture()
def diagnosed(session, run_ids):
    """A fully diagnosed golden incident -- Day 5's actual input."""
    result = run_incident_pipeline(session, _definition(run_ids))
    session.commit()
    return result


@pytest.fixture()
def ids(diagnosed) -> tuple[int, int]:
    return diagnosed.incident.incident_id, diagnosed.analysis_run_id


@pytest.fixture()
def executed_action(session, ids):
    """A completed flagship flow, executed and ready to verify."""
    incident_id, analysis_run_id = ids
    result = run_full_flow(
        session,
        incident_id,
        analysis_run_id,
        approver_identity=APPROVER,
        approve=True,
    )
    assert result.action is not None, "flagship flow must produce an action"
    assert result.action.status == "EXECUTED"
    session.flush()
    return result.action


# ---------------------------------------------------------------- the happy path
def test_flagship_action_verifies_as_effective(session, executed_action):
    """The flagship reroute genuinely helped, and verification says so."""
    result = verify_action(session, executed_action.action_id)

    assert result.status == VERIFICATION_COMPLETE
    assert result.outcome == RECOVERY_EFFECTIVE
    assert result.integrity_passed is True
    # Failure rate fell on the treated cohort.
    assert result.measured_failure_rate_improvement > 0
    # And the projection described what happened.
    assert result.attainment_ratio >= ATTAINMENT_EFFECTIVE


def test_verification_keeps_projection_and_measurement_apart(session, executed_action):
    """
    §10: expected outcome and actual outcome must never be merged.

    They may coincide numerically -- on this deterministic flagship they do -- but they
    are stored as separate fields with separate provenance, and a test that only checked
    equality would pass even if the code had collapsed them into one value.
    """
    result = verify_action(session, executed_action.action_id)
    row = session.get(Verification, result.verification_id)

    assert row.projected_success_delta is not None
    assert row.measured_success_delta is not None
    # Distinct columns, distinct sources.
    assert row.baseline_failure_rate is not None
    assert row.actual_failure_rate is not None
    assert float(row.baseline_failure_rate) != float(row.actual_failure_rate)

    # The projection comes from the simulation; the measurement from the adapter.
    action = session.get(Action, executed_action.action_id)
    assert float(row.projected_success_delta) == pytest.approx(
        action.expected_outcome["expected_success_delta"]
    )
    assert float(row.actual_failure_rate) == pytest.approx(
        action.actual_simulated_outcome["post_action_failure_rate"]
    )


def test_gmv_recovered_is_not_gmv_retained(session, executed_action):
    """
    §10 again, on the figure most likely to be conflated.

    `projected_gmv_retained` is what the simulation forecast; `actual_gmv_recovered` is
    what verification measured. Reporting one under the other's name would overstate the
    system's own results, so they are asserted to be genuinely different quantities.
    """
    result = verify_action(session, executed_action.action_id)
    assert result.projected_gmv_retained is not None
    assert result.actual_gmv_recovered is not None
    assert result.actual_gmv_recovered != result.projected_gmv_retained


# ---------------------------------------------------------------- it can say no
@pytest.mark.parametrize(
    "improvement, attainment, expected",
    [
        # No measurable movement at all.
        (0.0, None, RECOVERY_NOT_VERIFIED),
        # Movement below the meaningfulness floor is treated as noise.
        (MIN_MEANINGFUL_FAILURE_RATE_IMPROVEMENT / 2, 1.0, RECOVERY_NOT_VERIFIED),
        # A real improvement that badly missed its projection is NOT a success: the
        # model that authorised the action did not describe reality.
        (0.05, ATTAINMENT_FLOOR / 2, RECOVERY_NOT_VERIFIED),
        # Real, and short of the bar.
        (0.05, (ATTAINMENT_FLOOR + ATTAINMENT_EFFECTIVE) / 2, PARTIALLY_EFFECTIVE),
        # Real, and met the projection.
        (0.05, 1.0, RECOVERY_EFFECTIVE),
        # Beat the projection.
        (0.05, 1.4, RECOVERY_EFFECTIVE),
        # Movement with no projection to compare against is reported, not celebrated.
        (0.05, None, PARTIALLY_EFFECTIVE),
        # A negative movement -- the action made things worse.
        (-0.02, 1.0, RECOVERY_NOT_VERIFIED),
    ],
)
def test_classification_covers_every_outcome(improvement, attainment, expected):
    outcome, reasons = _classify(improvement, attainment, integrity_ok=True)
    assert outcome == expected
    assert reasons, "every verdict must carry its reasons"


def test_failed_integrity_blocks_a_good_looking_result():
    """
    Integrity is checked BEFORE merit.

    A number whose lineage does not hold up must never be graded on how good it looks,
    however impressive the movement.
    """
    outcome, reasons = _classify(0.99, 1.0, integrity_ok=False)
    assert outcome == RECOVERY_NOT_VERIFIED
    assert any("Integrity" in r for r in reasons)


def test_verification_owns_its_thresholds():
    """
    §48: verification must be independent of the layer that proposed the action.

    Enforced structurally -- the module must not import the policy gate or the
    recommendation builder, because borrowing their standards is what would make the
    verdict a restatement rather than a check.
    """
    import ast

    import aventum_verification.constants as c
    import aventum_verification.verify as v

    # Parsed, not grepped. Both modules DISCUSS the policy layer in their docstrings --
    # explaining why they do not borrow its thresholds is the whole point -- so a text
    # search would fail on the very comments that document the property.
    forbidden = {"aventum_policy", "aventum_action.recommendation"}
    for module in (c, v):
        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in forbidden, f"{module.__name__} imports {node.module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden, f"{module.__name__} imports {alias.name}"

    # And it must not reach the recommendation builder by any name.
    names = {
        n.id for n in ast.walk(ast.parse(open(v.__file__, encoding="utf-8").read()))
        if isinstance(n, ast.Name)
    }
    assert "build_recommendation" not in names


# ---------------------------------------------------------------- eligibility
def test_rejected_action_is_ineligible_not_failed(session, ids):
    """
    "There was nothing to measure" is a different statement from "we measured and it
    did not help", and conflating them would understate the system's success rate.
    """
    incident_id, analysis_run_id = ids
    result = run_full_flow(
        session,
        incident_id,
        analysis_run_id,
        approver_identity=APPROVER,
        approve=True,
    )
    action = result.action
    action.status = "REJECTED"
    action.executed_at = None
    action.rejection_reason = "forced for test"
    session.flush()

    verdict = verify_action(session, action.action_id)
    assert verdict.status == VERIFICATION_INELIGIBLE
    assert verdict.outcome is None
    assert verdict.ineligible_reason


# ---------------------------------------------------------------- idempotency
def test_verifying_twice_returns_the_same_verdict(session, executed_action):
    """
    §12/§27 of the red-team matrix: a duplicate state-changing call must be safe.

    Guaranteed by `uq_verification_identity`, so a second request converges on the
    stored verdict rather than minting a second opinion a UI could choose between.
    """
    first = verify_action(session, executed_action.action_id)
    second = verify_action(session, executed_action.action_id)

    assert first.verification_id == second.verification_id
    assert first.outcome == second.outcome

    count = session.scalar(
        text("SELECT count(*) FROM verifications WHERE action_id = :a"),
        {"a": executed_action.action_id},
    )
    assert count == 1


def test_verification_emits_exactly_one_audit_event(session, executed_action):
    verify_action(session, executed_action.action_id)
    verify_action(session, executed_action.action_id)

    events = session.scalars(
        text("SELECT event_type FROM audit_events WHERE event_type LIKE 'VERIFICATION%'")
    ).all()
    assert len(events) == 1


def test_get_verification_returns_none_before_verifying(session, executed_action):
    assert get_verification(session, executed_action.action_id) is None
    verify_action(session, executed_action.action_id)
    assert get_verification(session, executed_action.action_id) is not None


# ---------------------------------------------------------------- integrity checks
def test_population_check_compares_allocations_not_cohorts(session, executed_action):
    """
    Regression for a real defect found during Day 5 browser testing.

    The check originally compared the affected-cohort count against the full-window
    allocation total. Those are different populations by design, so it failed on every
    healthy run and turned a correct RECOVERY_EFFECTIVE into RECOVERY_NOT_VERIFIED.
    """
    result = verify_action(session, executed_action.action_id)
    check = next(c for c in result.integrity_checks if c["name"] == "POPULATION_STABLE")
    assert check["passed"] is True

    action = session.get(Action, executed_action.action_id)
    pre_total = sum(action.pre_action_metrics["current_distribution"].values())
    post_total = sum(action.actual_simulated_outcome["resulting_allocation"].values())
    assert pre_total == post_total
    # And the cohort really is smaller, which is why the naive comparison was wrong.
    assert action.pre_action_metrics["population"] < pre_total


def test_tampered_execution_fingerprint_fails_integrity(session, executed_action):
    """A recorded fingerprint that does not match its inputs must not be rendered as a tick."""
    action = session.get(Action, executed_action.action_id)
    action.execution_fingerprint = "0" * 64
    session.flush()

    result = verify_action(session, action.action_id)
    assert result.integrity_passed is False
    assert result.outcome == RECOVERY_NOT_VERIFIED
    check = next(c for c in result.integrity_checks if c["name"] == "EXECUTION_FINGERPRINT")
    assert check["passed"] is False


# ---------------------------------------------------------------- batch measurement
def test_batch_counts_come_from_persisted_rows(session, executed_action):
    verify_action(session, executed_action.action_id)
    summary = build_batch_summary(session)

    assert summary.interventions_proposed >= 1
    assert summary.approvals_granted >= 1
    assert summary.interventions_executed >= 1
    assert summary.interventions_verified >= 1
    assert summary.recovery_effective_count >= 1
    assert summary.total_projected_gmv_retained > 0
    assert summary.total_actual_gmv_recovered > 0
    # The two money figures answer different questions and must not coincide by accident.
    assert summary.total_actual_gmv_recovered != summary.total_projected_gmv_retained


def test_empty_batch_reports_unavailable_not_zero(session, diagnosed):
    """
    §19: a metric the data cannot support is UNAVAILABLE.

    A 0% verification success rate reads as "we tried and failed"; the truth before any
    run is "we have not tried yet", and a dashboard must not confuse the two.
    """
    summary = build_batch_summary(session)
    assert summary.verification_success_rate == "UNAVAILABLE"
    assert summary.recovery_uplift == "UNAVAILABLE"
    assert summary.intervention_rate == "UNAVAILABLE"


def test_unverified_recovery_contributes_no_gmv(session, executed_action):
    """An action verification could not confirm must not inflate the headline total."""
    action = session.get(Action, executed_action.action_id)
    # Force a not-verified outcome by breaking lineage integrity.
    action.execution_fingerprint = "0" * 64
    session.flush()

    result = verify_action(session, action.action_id)
    assert result.outcome == RECOVERY_NOT_VERIFIED

    summary = build_batch_summary(session)
    assert summary.recovery_not_verified_count == 1
    assert summary.total_actual_gmv_recovered == 0.0


# ---------------------------------------------------------------- demo reset
def test_demo_reset_clears_workflow_state(session, executed_action):
    verify_action(session, executed_action.action_id)
    report = reset_demo_state(session)

    assert report["reset"] is True
    for table in WORKFLOW_TABLES:
        remaining = session.scalar(text(f"SELECT count(*) FROM {table}"))
        assert remaining == 0, f"{table} should be empty after reset"


def test_demo_reset_never_touches_observed_data(session, executed_action):
    """
    §30/§38: a reset that could alter the canonical dataset would be a reset nobody
    should run. The 250,000 observed rows and all Day 3 analysis survive it.
    """
    before_txn = session.scalar(text("SELECT count(*) FROM transactions"))
    before_incidents = session.scalar(text("SELECT count(*) FROM incidents"))
    before_rca = session.scalar(text("SELECT count(*) FROM incident_rca_results"))

    reset_demo_state(session)

    assert session.scalar(text("SELECT count(*) FROM transactions")) == before_txn
    assert session.scalar(text("SELECT count(*) FROM incidents")) == before_incidents
    assert session.scalar(text("SELECT count(*) FROM incident_rca_results")) == before_rca


def test_reset_has_no_statement_capable_of_touching_protected_tables():
    """
    Structural, not behavioural: the reset's table list and the protected list must not
    overlap. A behavioural test only proves the tables it happens to check; this proves
    the module cannot name a protected table at all.
    """
    assert not (set(WORKFLOW_TABLES) & set(PROTECTED_TABLES))

    import aventum_api.demo as demo

    source = open(demo.__file__, encoding="utf-8").read()
    truncate_stmt = source[source.index('"TRUNCATE TABLE "') : source.index("session.flush()")]
    for protected in PROTECTED_TABLES:
        assert protected not in truncate_stmt


def test_reset_does_not_cascade():
    """
    TRUNCATE ... CASCADE is exactly how a reset reaches a table nobody intended, so its
    absence is asserted rather than assumed.
    """
    import aventum_api.demo as demo

    source = open(demo.__file__, encoding="utf-8").read()
    assert "CASCADE" not in source.upper().replace("# ", "").split('"""')[-1]


# ---------------------------------------------------------------- provenance
def test_verification_is_structurally_marked_simulated(session, executed_action):
    """The database itself refuses to record a verification of a real execution."""
    result = verify_action(session, executed_action.action_id)
    row = session.get(Verification, result.verification_id)
    assert row.is_simulated is True

    row.is_simulated = False
    with pytest.raises(Exception):
        session.flush()
    session.rollback()


def test_verification_records_its_own_limitations(session, executed_action):
    result = verify_action(session, executed_action.action_id)
    row = session.get(Verification, result.verification_id)

    assert row.limitations is not None
    # It must say plainly that no production money moved.
    assert "No production money" in row.limitations["recovery_claim"]
    # And that this is not a randomised control.
    assert "control" in row.limitations["control_group"].lower()
    assert row.metric_definitions is not None
    assert "failure_rate" in row.metric_definitions


def test_model_version_is_recorded(session, executed_action):
    result = verify_action(session, executed_action.action_id)
    row = session.get(Verification, result.verification_id)
    assert row.model_version == VERIFICATION_MODEL_VERSION
    assert row.verification_fingerprint
