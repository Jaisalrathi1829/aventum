"""
Day 5 API tests: the HTTP boundary, and the red-team matrix (§33, §42).

The theme of this file is that the browser is untrusted. Most tests here assert a
REFUSAL: that a forged frontend cannot approve, execute, bypass policy, or reach past
the API into the database. A green result from any of them would be a security finding.

These use FastAPI's TestClient with the app's session dependency overridden onto the
test database, so the real route handlers, real validation and real Day 4A modules run.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from aventum_action.approval import ApprovalError, decide_approval, request_approval
from aventum_action.models import Approval, Recommendation
from aventum_action.pipeline import run_decision_pipeline, run_full_flow
from aventum_api.app import app
from aventum_api.deps import get_session
from aventum_incident.pipeline import run_incident_pipeline
from aventum_ingest.pipeline import run_ingestion
from aventum_synth.generator import run_generation
from aventum_verification.verify import verify_action
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


# ============================================================ concurrency (AV-02, AV-05)
# These use REAL THREADS and SEPARATE SESSIONS on the same engine. The `client` fixture
# above overrides `get_session` with a single shared session, which cannot reproduce a
# race: one session means one connection means no contention. A concurrency regression
# written against that fixture would pass whether or not the guard exists, so it would be
# worse than no test at all.
def _parallel(n, fn):
    """Fire n callables as simultaneously as a barrier allows; return their results."""
    out = [None] * n
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        try:
            out[i] = ("ok", fn())
        except Exception as exc:  # noqa: BLE001 - the point is to observe which lose
            out[i] = ("err", exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def test_a_stale_reader_cannot_record_a_second_approval_decision(api_engine, incident):
    """
    AV-02 regression, written to be DETERMINISTIC rather than timing-dependent.

    The defect is a read-then-write: a caller loads the approval, someone else decides
    it, and the first caller writes anyway because its in-memory copy still says PENDING.
    A threaded version of this test passes with or without the fix -- the threads
    serialise naturally at the Python level, and only the wider HTTP request window
    exposed the race. So this reproduces the exact interleaving instead of hoping for it:

        session A loads the approval        (status PENDING in A's identity map)
        session B decides and commits       (status APPROVED in the database)
        session A decides                   <- must refuse, not overwrite

    Without `with_for_update(...).execution_options(populate_existing=True)`, A never
    re-reads and happily emits a second APPROVAL_DECIDED event.
    """
    with Session(api_engine) as setup:
        row = setup.execute(
            text("SELECT analysis_run_id FROM incident_analysis_runs "
                 "WHERE incident_id = :i ORDER BY analysis_run_id DESC LIMIT 1"),
            {"i": incident},
        ).first()
        decision = run_decision_pipeline(setup, incident, int(row[0]))
        approval = request_approval(setup, decision.recommendation, decision.decision)
        setup.commit()
        approval_id = approval.approval_id

    session_a = Session(api_engine)
    session_b = Session(api_engine)
    try:
        # A loads the approval first and caches PENDING.
        stale = session_a.get(Approval, approval_id)
        assert stale.status == "PENDING"

        # B decides and commits underneath A.
        decide_approval(
            session_b, session_b.get(Approval, approval_id),
            decision="APPROVED", approver_identity=APPROVER, note="winner",
        )
        session_b.commit()

        # A must now refuse on the CURRENT row, not on its stale copy.
        with pytest.raises(ApprovalError) as raised:
            decide_approval(
                session_a, stale,
                decision="REJECTED", approver_identity="stale.reader@aventum.test",
            )
        assert "already" in str(raised.value)
        session_a.rollback()
    finally:
        session_a.close()
        session_b.close()

    with Session(api_engine) as check:
        events = check.execute(
            text("SELECT count(*) FROM audit_events WHERE event_type = 'APPROVAL_DECIDED' "
                 "AND output_ref->>'id' = :a"),
            {"a": str(approval_id)},
        ).scalar()
        final = check.execute(
            text("SELECT status, approver_identity FROM approvals WHERE approval_id = :a"),
            {"a": approval_id},
        ).first()

    assert events == 1, f"one decision must leave one audit event, got {events}"
    # The winner's decision stands; the stale REJECTED never lands.
    assert final[0] == "APPROVED"
    assert final[1] == APPROVER


def test_concurrent_approval_decisions_leave_one_event(api_engine, incident):
    """
    The realistic companion to the deterministic test above: five threads, five
    sessions, one approval. Not guaranteed to interleave, so it is an additional
    assurance rather than the load-bearing guard.
    """
    with Session(api_engine) as setup:
        row = setup.execute(
            text("SELECT analysis_run_id FROM incident_analysis_runs "
                 "WHERE incident_id = :i ORDER BY analysis_run_id DESC LIMIT 1"),
            {"i": incident},
        ).first()
        decision = run_decision_pipeline(setup, incident, int(row[0]))
        approval = request_approval(setup, decision.recommendation, decision.decision)
        setup.commit()
        approval_id = approval.approval_id

    def decide():
        with Session(api_engine) as s:
            decide_approval(
                s, s.get(Approval, approval_id),
                decision="APPROVED", approver_identity=APPROVER, note="concurrent",
            )
            s.commit()

    results = _parallel(5, decide)
    winners = [r for r in results if r[0] == "ok"]
    assert len(winners) == 1, f"exactly one decision may win, got {len(winners)}"
    for _, exc in [r for r in results if r[0] == "err"]:
        assert isinstance(exc, ApprovalError), f"loser raised {type(exc).__name__}: {exc}"

    with Session(api_engine) as check:
        events = check.execute(
            text("SELECT count(*) FROM audit_events WHERE event_type = 'APPROVAL_DECIDED' "
                 "AND output_ref->>'id' = :a"),
            {"a": str(approval_id)},
        ).scalar()
    assert events == 1


def test_a_losing_verifier_receives_the_winners_verdict(api_engine, incident):
    """
    AV-05 regression, driven to the exact interleaving rather than hoping for it.

    `uq_verification_identity` always protected the DATA -- only one row was ever
    written. What escaped was the IntegrityError, surfacing to the caller as an
    unhandled 500: the loser was told its request failed when the work was complete and
    the answer was sitting in the table.

    The interleaving is forced with two events:

        A: verify + flush (row present but UNCOMMITTED) -> signals `inserted`
        B: verify -> its SELECT sees nothing -> inserts -> BLOCKS on the unique index
        A: commits -> B's insert finally raises IntegrityError -> must be handled

    Postgres blocks the second inserter on a unique index until the first transaction
    resolves, which is what makes this reliable rather than timing-dependent.
    """
    with Session(api_engine) as setup:
        row = setup.execute(
            text("SELECT analysis_run_id FROM incident_analysis_runs "
                 "WHERE incident_id = :i ORDER BY analysis_run_id DESC LIMIT 1"),
            {"i": incident},
        ).first()
        flow = run_full_flow(
            setup, incident, int(row[0]), approver_identity=APPROVER, approve=True
        )
        setup.commit()
        action_id = flow.action.action_id

    inserted = threading.Event()
    may_commit = threading.Event()
    loser_result: dict = {}

    def winner():
        with Session(api_engine) as sa:
            verify_action(sa, action_id)
            sa.flush()                 # row exists, still uncommitted
            inserted.set()
            may_commit.wait(20)
            sa.commit()

    def loser():
        inserted.wait(20)
        with Session(api_engine) as sb:
            try:
                # Blocks on the unique index until the winner commits, then collides.
                loser_result["value"] = verify_action(sb, action_id)
                sb.commit()
            except Exception as exc:   # noqa: BLE001 - the whole point is that none escapes
                loser_result["error"] = exc

    t_win = threading.Thread(target=winner)
    t_lose = threading.Thread(target=loser)
    t_win.start()
    t_lose.start()
    inserted.wait(20)
    # Give the loser time to reach its INSERT and BLOCK on the unique index before the
    # winner commits. Without this pause the winner commits first, the loser's SELECT
    # then finds the committed row, and the collision path is never exercised -- the
    # test would pass with or without the fix, which is worse than having no test.
    time.sleep(2)
    may_commit.set()
    t_win.join(30)
    t_lose.join(30)

    assert "error" not in loser_result, (
        f"the losing verifier must not raise: {loser_result.get('error')!r}"
    )
    assert loser_result.get("value") is not None, "the loser must receive a verdict"

    with Session(api_engine) as check:
        rows = check.execute(
            text("SELECT count(*) FROM verifications WHERE action_id = :a"), {"a": action_id}
        ).scalar()
        events = check.execute(
            text("SELECT count(*) FROM audit_events WHERE event_type LIKE 'VERIFICATION%'")
        ).scalar()
        stored = check.execute(
            text("SELECT verification_id, outcome FROM verifications WHERE action_id = :a"),
            {"a": action_id},
        ).first()

    assert rows == 1, f"exactly one verification row, got {rows}"
    assert events == 1, f"exactly one verification audit event, got {events}"
    # And the loser was handed the row that actually exists, not a second opinion.
    assert loser_result["value"].verification_id == stored[0]
    assert loser_result["value"].outcome == stored[1]


# ============================================================ degraded mode (AV-04)
def test_engine_bounds_how_long_a_dead_database_may_block():
    """
    AV-04 regression: a database that is not answering must be discovered, not waited on.

    Without these bounds every endpoint hung for over 120 seconds with PostgreSQL
    stopped -- including `/api/health`, whose only job is to report that the database is
    down. It could not, because it blocked trying to ask it.

    2s is libpq's floor, not a preference: smaller values are silently promoted.
    """
    from aventum_api.deps import DB_CONNECT_TIMEOUT_S, DB_POOL_TIMEOUT_S, get_engine

    assert 0 < DB_CONNECT_TIMEOUT_S <= 5
    assert 0 < DB_POOL_TIMEOUT_S <= 10

    engine = get_engine()
    assert engine.pool._timeout == DB_POOL_TIMEOUT_S

    # Assert the OBSERVABLE property, not a private attribute: an engine built the same
    # way must give up quickly on a port nobody is listening on. Before the fix this
    # blocked for over two minutes; the bound below would still have failed it by a wide
    # margin, while staying generous enough not to be flaky on a slow machine.
    import time

    from sqlalchemy import create_engine as _create_engine

    dead = _create_engine(
        "postgresql+psycopg://aventum:x@127.0.0.1:1/aventum",
        connect_args={"connect_timeout": DB_CONNECT_TIMEOUT_S},
        pool_timeout=DB_POOL_TIMEOUT_S,
    )
    started = time.perf_counter()
    with pytest.raises(Exception):
        with dead.connect():
            pass
    elapsed = time.perf_counter() - started
    dead.dispose()
    assert elapsed < 15, f"a dead database took {elapsed:.1f}s to fail; it must fail fast"


def test_health_reports_a_failed_database_instead_of_raising():
    """
    The health probe must convert a database failure into a REPORT, never an exception.

    If it raised, health would fail exactly when it is most needed, and the operator
    would be told the API is unreachable when the API is the one component still working.
    """
    from aventum_api.app import _database_available

    class _Dead:
        def execute(self, *_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    ok, detail = _database_available(_Dead())
    assert ok is False
    assert detail == "unreachable"

    class _Alive:
        def execute(self, *_args, **_kwargs):
            return None

    ok, detail = _database_available(_Alive())
    assert ok is True
    assert detail == "connected"


# ============================================================ agent unavailable (AV-07)
def test_agent_unavailable_returns_503_not_200(client, incident, monkeypatch):
    """
    AV-07 regression: a failed agent run must not report HTTP success.

    The loop CATCHES `AgentUnavailable` and returns it as an outcome, so the route's
    `except AgentUnavailable` branch never fired and the endpoint answered 200 with
    `status: AGENT_UNAVAILABLE` in the body. Honest prose, wrong status line -- a client
    branching on the status code was told a failed operation had succeeded.
    """
    import aventum_agent.service as service

    class _Outcome:
        status = "AGENT_UNAVAILABLE"
        final_state = "ABANDONED"
        agent_run_id = None
        recommendation_id = None
        approval_id = None

    class _Analysis:
        outcome = _Outcome()

    monkeypatch.setattr(service, "analyze_incident", lambda *a, **k: _Analysis())

    response = client.post(f"/api/incidents/{incident}/agent/analyze")
    assert response.status_code == 503, f"expected 503, got {response.status_code}"
    body = response.json()["detail"]
    assert body["code"] == "AGENT_UNAVAILABLE"
    # The message must keep telling the operator what still works.
    assert "Deterministic" in body["message"]


def test_agent_failure_fabricates_nothing(client, incident, monkeypatch):
    """A failed agent run must leave no invented recommendation or rationale behind."""
    import aventum_agent.service as service

    class _Outcome:
        status = "AGENT_UNAVAILABLE"
        final_state = "ABANDONED"
        agent_run_id = None
        recommendation_id = None
        approval_id = None

    class _Analysis:
        outcome = _Outcome()

    monkeypatch.setattr(service, "analyze_incident", lambda *a, **k: _Analysis())
    client.post(f"/api/incidents/{incident}/agent/analyze")

    body = client.get(f"/api/incidents/{incident}/recommendation").json()
    assert body["recommendation"] is None
    agent = client.get(f"/api/incidents/{incident}/agent").json()
    assert agent["agent_run"] is None
