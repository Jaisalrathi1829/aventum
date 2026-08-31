"""
Day 5 API tests: the HTTP boundary, and the red-team matrix (§33, §42).

The theme of this file is that the browser is untrusted. Most tests here assert a
REFUSAL: that a forged frontend cannot approve, execute, bypass policy, or reach past
the API into the database. A green result from any of them would be a security finding.

These use FastAPI's TestClient with the app's session dependency overridden onto the
test database, so the real route handlers, real validation and real Day 4A modules run.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from aventum_action.models import Approval, Recommendation
from aventum_api.app import app
from aventum_api.deps import get_session
from aventum_incident.pipeline import run_incident_pipeline
from aventum_ingest.pipeline import run_ingestion
from aventum_synth.generator import run_generation
from tests.test_incident_intelligence import _definition, _fixture_rows

APPROVER = "api.tester@aventum.test"


@pytest.fixture()
def api_engine(engine, registered_source):
    run_ingestion(engine, registered_source(_fixture_rows(), name="day5-api-fixture.csv"))
    result = run_generation(engine, generation_seed="day4-test-seed")
    assert result.succeeded
    return engine


@pytest.fixture()
def api_session(api_engine) -> Session:
    with Session(api_engine) as sess:
        yield sess


@pytest.fixture()
def incident(api_session):
    row = api_session.execute(
        text(
            "SELECT generation_run_id, source_ingestion_run_id "
            "FROM synthetic_generation_runs WHERE status = 'SUCCEEDED' "
            "ORDER BY generation_run_id DESC LIMIT 1"
        )
    ).mappings().first()
    result = run_incident_pipeline(
        api_session, _definition((int(row["generation_run_id"]), int(row["source_ingestion_run_id"])))
    )
    api_session.commit()
    return result.incident.incident_id


@pytest.fixture()
def client(api_session):
    """
    The real app, pointed at the test database.

    The session dependency is overridden rather than the engine, so every route runs its
    own real logic and the transaction semantics of `get_session` are preserved.
    """
    def _session_override():
        yield api_session

    app.dependency_overrides[get_session] = _session_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ============================================================ reads
def test_health_reports_components_separately(client):
    body = client.get("/api/health").json()
    assert body["api"]["ok"] is True
    assert "database" in body and "agent" in body
    # The agent is explicitly optional: deterministic analysis does not depend on it.
    assert body["agent"]["required"] is False
    assert body["environment"]["mode"] == "SIMULATION MODE"


def test_overview_labels_its_environment(client, incident):
    body = client.get("/api/overview").json()
    assert body["environment"]["no_production_execution"] is True
    assert body["environment"]["capacity"] == "UNAVAILABLE"
    assert any(i["incident_id"] == incident for i in body["incidents"])


def test_incident_detail_preserves_primary_and_derivative(client, incident):
    """Day 3's P1-1 alert-role distinction must survive to the wire."""
    body = client.get(f"/api/incidents/{incident}").json()
    assert body["rca"] is not None
    assert all(d["alert_role"] == "PRIMARY" for d in body["detections"])
    assert all(d["alert_role"] == "DERIVATIVE" for d in body["derivative_detections"])


def test_unknown_incident_is_a_clean_404(client):
    response = client.get("/api/incidents/999999")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


# ============================================================ the flagship flow
def _analyze(client, incident):
    response = client.post(f"/api/incidents/{incident}/analyze")
    assert response.status_code == 200
    return response.json()["recommendation"]


def _approve(client, recommendation_id, decision="APPROVED"):
    approval = client.post(f"/api/recommendations/{recommendation_id}/approval-request").json()[
        "approval"
    ]
    response = client.post(
        f"/api/approvals/{approval['approval_id']}/decision",
        json={"decision": decision, "approver_identity": APPROVER},
    )
    assert response.status_code == 200
    return response.json()["approval"]


def test_full_flagship_flow_through_the_api(client, incident):
    rec = _analyze(client, incident)
    assert rec["policy"]["validation_result"] == "PERMITTED"
    assert rec["action_type"] == "REROUTE"

    approval = _approve(client, rec["recommendation_id"])
    assert approval["status"] == "APPROVED"
    assert approval["approver_identity"] == APPROVER

    execution = client.post(
        f"/api/recommendations/{rec['recommendation_id']}/execute",
        json={"executed_by": APPROVER},
    ).json()
    assert execution["action"]["status"] == "EXECUTED"
    action_id = execution["action"]["action_id"]

    verification = client.post(f"/api/actions/{action_id}/verify").json()["verification"]
    assert verification["status"] == "COMPLETE"
    assert verification["outcome"] in (
        "RECOVERY_EFFECTIVE",
        "PARTIALLY_EFFECTIVE",
        "RECOVERY_NOT_VERIFIED",
    )
    # Projection and measurement arrive as separate objects, never merged.
    assert "projected" in verification and "actual_simulated" in verification
    assert verification["projected"]["truth"] == "PROJECTED"
    assert verification["actual_simulated"]["truth"] == "SIMULATED"


def test_recovery_state_is_derived_not_stored(client, incident):
    """
    §24: a second reader must observe the same authoritative state.

    Nothing is cached client-side, and the state is recomputed from persisted rows on
    every request -- so two identical calls at different points in the flow must track
    the database, not a session.
    """
    assert client.get("/api/overview").json()["recovery"]["state"] == "NO_ACTIVE_ACTION"

    rec = _analyze(client, incident)
    state = client.get(f"/api/incidents/{incident}/recommendation").json()["recovery"]["state"]
    assert state == "AWAITING_APPROVAL_REQUEST"

    _approve(client, rec["recommendation_id"])
    state = client.get(f"/api/incidents/{incident}/recommendation").json()["recovery"]["state"]
    assert state == "APPROVED"


# ============================================================ red team: authority
def test_execution_without_approval_is_refused(client, incident):
    """§22/§23: the browser cannot execute what no human approved."""
    rec = _analyze(client, incident)
    response = client.post(f"/api/recommendations/{rec['recommendation_id']}/execute", json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NO_APPROVAL"


def test_approval_without_an_identity_is_refused(client, incident):
    """An approval with no attributable human is not an approval."""
    rec = _analyze(client, incident)
    approval = client.post(
        f"/api/recommendations/{rec['recommendation_id']}/approval-request"
    ).json()["approval"]

    response = client.post(
        f"/api/approvals/{approval['approval_id']}/decision", json={"decision": "APPROVED"}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "APPROVER_REQUIRED"


def test_a_forged_decision_value_is_refused(client, incident):
    """§16/§24: client-supplied state is validated, never trusted."""
    rec = _analyze(client, incident)
    approval = client.post(
        f"/api/recommendations/{rec['recommendation_id']}/approval-request"
    ).json()["approval"]

    for forged in ("EXECUTED", "VERIFIED", "yes", "", "APPROVED; DROP TABLE actions"):
        response = client.post(
            f"/api/approvals/{approval['approval_id']}/decision",
            json={"decision": forged, "approver_identity": APPROVER},
        )
        assert response.status_code == 400, f"{forged!r} must be refused"
        assert response.json()["detail"]["code"] == "INVALID_DECISION"


def test_rejected_approval_blocks_execution(client, incident):
    rec = _analyze(client, incident)
    approval = _approve(client, rec["recommendation_id"], decision="REJECTED")
    assert approval["status"] == "REJECTED"

    response = client.post(f"/api/recommendations/{rec['recommendation_id']}/execute", json={})
    assert response.status_code == 409


def test_duplicate_approval_request_is_refused(client, incident):
    """§12 of the red-team matrix: an approval cannot be raced or duplicated."""
    rec = _analyze(client, incident)
    first = client.post(f"/api/recommendations/{rec['recommendation_id']}/approval-request")
    assert first.status_code == 200
    second = client.post(f"/api/recommendations/{rec['recommendation_id']}/approval-request")
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "APPROVAL_NOT_PERMITTED"


def test_duplicate_decision_is_refused(client, incident):
    rec = _analyze(client, incident)
    approval = client.post(
        f"/api/recommendations/{rec['recommendation_id']}/approval-request"
    ).json()["approval"]

    body = {"decision": "APPROVED", "approver_identity": APPROVER}
    assert client.post(f"/api/approvals/{approval['approval_id']}/decision", json=body).status_code == 200
    second = client.post(f"/api/approvals/{approval['approval_id']}/decision", json=body)
    assert second.status_code == 409


def test_duplicate_execution_is_idempotent(client, incident):
    """
    §13/§27: a double-submitted execution must not act twice.

    Guaranteed by `uq_action_idempotency` in the database, so two requests converge on
    one action rather than producing two reroutes.
    """
    rec = _analyze(client, incident)
    _approve(client, rec["recommendation_id"])

    first = client.post(
        f"/api/recommendations/{rec['recommendation_id']}/execute", json={"executed_by": APPROVER}
    ).json()
    second = client.post(
        f"/api/recommendations/{rec['recommendation_id']}/execute", json={"executed_by": APPROVER}
    ).json()

    assert first["action"]["action_id"] == second["action"]["action_id"]


def test_duplicate_verification_is_idempotent(client, incident):
    rec = _analyze(client, incident)
    _approve(client, rec["recommendation_id"])
    action_id = client.post(
        f"/api/recommendations/{rec['recommendation_id']}/execute", json={"executed_by": APPROVER}
    ).json()["action"]["action_id"]

    first = client.post(f"/api/actions/{action_id}/verify").json()["verification"]
    second = client.post(f"/api/actions/{action_id}/verify").json()["verification"]
    assert first["verification_id"] == second["verification_id"]


# ============================================================ red team: exposure
def test_api_exposes_no_sql_or_credentials(client, incident):
    """
    §28: the browser must never receive SQL, a connection string, or a credential.

    Checked across every read endpoint's real payload rather than by inspection, because
    a serializer could start leaking one at any time.
    """
    _analyze(client, incident)
    paths = [
        "/api/health",
        "/api/overview",
        f"/api/incidents/{incident}",
        f"/api/incidents/{incident}/simulations",
        f"/api/incidents/{incident}/recommendation",
        f"/api/incidents/{incident}/audit",
        f"/api/incidents/{incident}/agent",
        "/api/batch/recovery",
    ]
    forbidden = ("postgresql://", "postgresql+psycopg", "SELECT ", "INSERT ", "password", "aventum_local_dev")
    for path in paths:
        body = client.get(path).text
        for needle in forbidden:
            assert needle not in body, f"{path} leaked {needle!r}"


def test_internal_errors_do_not_leak_details(client):
    """§29: no stack trace, no SQL, no database detail reaches the browser."""
    response = client.get("/api/actions/999999")
    assert response.status_code == 404
    assert "Traceback" not in response.text
    assert "psycopg" not in response.text


def test_no_endpoint_accepts_arbitrary_sql(client):
    """There is no query endpoint. This asserts the absence rather than assuming it."""
    routes = {getattr(r, "path", "") for r in app.routes}
    for suspicious in ("/api/query", "/api/sql", "/api/execute-sql", "/api/db"):
        assert suspicious not in routes


# ============================================================ stopping behaviour
def test_no_action_incident_reports_a_stop_not_a_failure(client, api_session, incident):
    """
    §20/§33-E: NO_ACTION is a successful terminal state.

    Forced here by rewriting the persisted recommendation, because the flagship fixture
    deliberately warrants acting -- the point is that the API reports the stop correctly
    when it happens, not that this particular incident produces one.
    """
    rec = _analyze(client, incident)
    row = api_session.get(Recommendation, rec["recommendation_id"])
    # `ck_rec_action_shape` requires a NO_ACTION row to carry no route and no traffic --
    # the database refuses an incoherent recommendation, so the forced state has to be a
    # valid one rather than just a relabelled REROUTE.
    row.action_type = "NO_ACTION"
    row.source_gateway_id = None
    row.target_gateway_id = None
    row.traffic_percentage = 0
    api_session.flush()

    body = client.get(f"/api/incidents/{incident}/recommendation").json()
    assert body["recovery"]["state"] == "NO_ACTION"
    assert "no action" in body["recovery"]["detail"].lower()


def test_policy_blocked_recommendation_never_reaches_approval(client, api_session, incident):
    """§34/§11 of the red-team matrix: a blocked proposal must not be presentable."""
    rec = _analyze(client, incident)
    row = api_session.get(Recommendation, rec["recommendation_id"])
    # `ck_rec_reason_codes_coherent` requires a BLOCKED recommendation to say WHY. A
    # refusal with no stated reason is exactly what that constraint exists to prevent.
    row.policy_validation_result = "BLOCKED"
    row.policy_reason_codes = {"MIN_SIGNIFICANCE_SIGMA": "BLOCKED: forced for test"}
    api_session.flush()

    body = client.get(f"/api/incidents/{incident}/recommendation").json()
    assert body["recovery"]["state"] == "POLICY_BLOCKED"

    response = client.post(f"/api/recommendations/{rec['recommendation_id']}/approval-request")
    assert response.status_code == 409


def test_expired_approval_cannot_be_decided(client, api_session, incident):
    """§37/§6 of the red-team matrix."""
    from datetime import datetime, timedelta, timezone

    rec = _analyze(client, incident)
    approval_id = client.post(
        f"/api/recommendations/{rec['recommendation_id']}/approval-request"
    ).json()["approval"]["approval_id"]

    row = api_session.get(Approval, approval_id)
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    api_session.flush()

    response = client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"decision": "APPROVED", "approver_identity": APPROVER},
    )
    assert response.status_code == 409


def test_staleness_is_reported_for_an_expired_recommendation(client, api_session, incident):
    """§26: a stale recommendation must never appear executable."""
    from datetime import datetime, timedelta, timezone

    rec = _analyze(client, incident)
    row = api_session.get(Recommendation, rec["recommendation_id"])
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    api_session.flush()

    stale = client.get(f"/api/incidents/{incident}/recommendation").json()["stale"]
    assert stale["is_stale"] is True
    assert stale["reasons"]
    assert stale["next_step"]


# ============================================================ empty states
def test_incident_with_no_recommendation_returns_explicit_nulls(client, incident):
    """§33-R: an empty result is explicit, never a fabricated placeholder."""
    body = client.get(f"/api/incidents/{incident}/recommendation").json()
    assert body["recommendation"] is None
    assert body["approval"] is None
    assert body["action"] is None
    assert body["verification"] is None


def test_empty_audit_is_an_empty_list(client, incident):
    body = client.get(f"/api/incidents/{incident}/audit").json()
    assert body["events"] == []


def test_agent_absence_is_stated_not_invented(client, incident):
    """§27/§21: with no agent run, the API says so rather than inventing activity."""
    body = client.get(f"/api/incidents/{incident}/agent").json()
    assert body["agent_run"] is None
    assert "no agent run" in body["detail"].lower()


# ============================================================ audit completeness
def test_flagship_run_produces_a_complete_audit_chain(client, incident):
    """
    §23: a visually complete timeline is not enough -- the persisted chain must hold.

    Asserts every required lifecycle event exists, that ordering is possible (ids
    increase monotonically with the lifecycle), and that the human decision is attributed
    to a person rather than to the system.
    """
    rec = _analyze(client, incident)
    _approve(client, rec["recommendation_id"])
    action_id = client.post(
        f"/api/recommendations/{rec['recommendation_id']}/execute", json={"executed_by": APPROVER}
    ).json()["action"]["action_id"]
    client.post(f"/api/actions/{action_id}/verify")

    events = client.get(f"/api/incidents/{incident}/audit").json()["events"]
    by_type = {e["event_type"]: e for e in events}

    for required in (
        "SIMULATION_COMPLETED",
        "POLICY_VALIDATED",
        "RECOMMENDATION_CREATED",
        "APPROVAL_REQUESTED",
        "APPROVAL_DECIDED",
        "ACTION_EXECUTED",
        "VERIFICATION_COMPLETED",
    ):
        assert required in by_type, f"missing lifecycle event {required}"

    # Impossible ordering would mean the chain cannot be reconstructed.
    order = [
        by_type[t]["event_id"]
        for t in (
            "RECOMMENDATION_CREATED",
            "APPROVAL_REQUESTED",
            "APPROVAL_DECIDED",
            "ACTION_EXECUTED",
            "VERIFICATION_COMPLETED",
        )
    ]
    assert order == sorted(order), "lifecycle events are out of order"

    # The human decision is attributed to a human.
    assert by_type["APPROVAL_DECIDED"]["actor"].startswith("HUMAN:")
    assert APPROVER in by_type["APPROVAL_DECIDED"]["actor"]

    # References resolve to real rows.
    assert by_type["RECOMMENDATION_CREATED"]["output_ref"]["table"] == "recommendations"
    assert by_type["VERIFICATION_COMPLETED"]["output_ref"]["verification_id"] is not None


def test_no_chain_of_thought_is_stored(client, incident):
    """§21/§22: structured summaries only. Reasoning traces are never persisted."""
    rec = _analyze(client, incident)
    _approve(client, rec["recommendation_id"])
    events = client.get(f"/api/incidents/{incident}/audit").json()["events"]

    for event in events:
        payload = str(event.get("payload") or "")
        for marker in ("<think>", "chain_of_thought", "reasoning_trace", "Let me think"):
            assert marker not in payload


# ============================================================ demo reset
def test_demo_reset_restores_a_clean_flagship_state(client, incident):
    rec = _analyze(client, incident)
    _approve(client, rec["recommendation_id"])

    body = client.post("/api/demo/reset").json()
    assert body["reset"] is True
    assert body["preserved"]["incidents"] >= 1

    after = client.get(f"/api/incidents/{incident}/recommendation").json()
    assert after["recommendation"] is None
    assert after["recovery"]["state"] == "NO_ACTIVE_ACTION"


def test_demo_reset_is_repeatable(client, incident):
    """A judge must be able to restart the demo more than once."""
    for _ in range(3):
        rec = _analyze(client, incident)
        _approve(client, rec["recommendation_id"])
        assert client.post("/api/demo/reset").json()["reset"] is True
