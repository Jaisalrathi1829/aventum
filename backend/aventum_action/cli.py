"""
Day 4A command-line interface — including the human approval surface.

    python -m aventum_action.cli decide <analysis_run_id>     # simulate -> recommend
    python -m aventum_action.cli payload <recommendation_id>  # what a human is shown
    python -m aventum_action.cli request <recommendation_id>  # raise a pending approval
    python -m aventum_action.cli approve <approval_id> --approver <name>
    python -m aventum_action.cli reject  <approval_id> --approver <name>
    python -m aventum_action.cli execute <recommendation_id> <approval_id>
    python -m aventum_action.cli status                       # recommendations/approvals
    python -m aventum_action.cli audit <incident_id>          # the append-only trail
    python -m aventum_action.cli verify <action_id>           # the Day 5 handoff as JSON

WHY APPROVAL IS THREE SEPARATE COMMANDS
----------------------------------------
`request`, `approve`, and `execute` are deliberately not one command with flags. A single
`--auto-approve` switch would be exactly the affordance that erodes a human gate: it
exists, it is convenient, and eventually it is always on. Separating them means a human
must issue a distinct command, naming themselves, between the machine proposing and the
machine acting.

`--approver` is mandatory on both decision commands. An approval with no human attached
is not an approval, and the database enforces the same rule.

Day 4A has no frontend and needs none — this IS the approval interface.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from aventum_ingest.config import load_config
from aventum_ingest.db import build_engine, build_session_factory, session_scope

from .approval import build_approval_payload, decide_approval, request_approval
from .execute import execute_action
from .handoff import build_verification_handoff, provenance_chain
from .models import Action, Approval, AuditEvent, Recommendation
from .pipeline import primary_alert_role, run_decision_pipeline, simulation_summary

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PRECONDITION = 4


def _json(value) -> str:
    return json.dumps(value, indent=2, default=str)


def cmd_decide(args) -> int:
    """Run the deterministic spine up to (not through) the approval boundary."""
    config = load_config()
    factory = build_session_factory(build_engine(config.database_url))
    with session_scope(factory) as session:
        from aventum_incident.models import IncidentAnalysisRun

        run = session.get(IncidentAnalysisRun, args.analysis_run_id)
        if run is None or run.incident_id is None:
            print(f"analysis run {args.analysis_run_id} does not resolve to an incident")
            return EXIT_PRECONDITION

        result = run_decision_pipeline(session, run.incident_id, args.analysis_run_id)

        print(f"\n=== Day 4A decision: incident {run.incident_id} ===")
        print(f"Cohort            : {result.sweep.no_action.affected_population:,} transactions")
        print(f"Alert role        : {result.alert_role}")
        print(f"\nCandidates ({len(result.sweep.candidates) + 1} simulated):")
        print(f"  {'candidate':<44} {'status':<20} {'GMV retained':>14} {'succ delta':>11}")
        for sim in [result.sweep.no_action] + result.sweep.candidates:
            s = simulation_summary(sim)
            marker = " <-- SELECTED" if sim.simulation_id == result.sweep.best.simulation_id else ""
            print(
                f"  {s['candidate']:<44} {s['status']:<20} "
                f"{s['expected_gmv_retained']:>14,.2f} {s['expected_success_delta']:>+11.4f}"
                f"{marker}"
            )

        print(f"\nSelection         : {result.sweep.selection_reason}")
        print(f"Policy            : {result.decision.result}")
        if not result.decision.permitted:
            print(f"  reason codes    : {', '.join(result.decision.reason_codes)}")
        for gate in result.decision.gates:
            flag = "PASS" if gate.passed else "FAIL"
            print(f"  [{flag}] {gate.name:<28} observed={gate.observed!r} required={gate.required!r}")

        rec = result.recommendation
        print(f"\nRecommendation    : {rec.recommendation_id} ({rec.action_type}, {rec.status})")
        print(f"  numbers sourced : counterfactual_simulations#{rec.simulation_id}")
        print(f"  rationale       : {rec.rationale!r}  (NULL — Day 4A runs without an agent)")
        print(f"  expires_at      : {rec.expires_at.isoformat()}")
        if result.requires_approval:
            print(
                f"\nNEXT: a human must approve.\n"
                f"  python -m aventum_action.cli request {rec.recommendation_id}"
            )
        else:
            print("\nNO_ACTION selected — terminal at PERMITTED. No approval, no execution.")
        print(f"\nElapsed           : {result.elapsed_ms:.1f} ms  {result.timings}")
    return EXIT_OK


def cmd_payload(args) -> int:
    """Print exactly what a human approver is shown."""
    config = load_config()
    factory = build_session_factory(build_engine(config.database_url))
    with session_scope(factory) as session:
        rec = session.get(Recommendation, args.recommendation_id)
        if rec is None:
            print(f"no recommendation {args.recommendation_id}")
            return EXIT_PRECONDITION
        print(_json(build_approval_payload(session, rec)))
    return EXIT_OK


def cmd_request(args) -> int:
    config = load_config()
    factory = build_session_factory(build_engine(config.database_url))
    with session_scope(factory) as session:
        rec = session.get(Recommendation, args.recommendation_id)
        if rec is None:
            print(f"no recommendation {args.recommendation_id}")
            return EXIT_PRECONDITION
        try:
            approval = request_approval(session, rec)
        except Exception as exc:
            print(f"cannot request approval: {exc}")
            return EXIT_PRECONDITION
        print(f"approval {approval.approval_id} PENDING, expires {approval.expires_at.isoformat()}")
        print(f"\n{_json(approval.payload)}")
        print(
            f"\nA HUMAN must now decide:\n"
            f"  python -m aventum_action.cli approve {approval.approval_id} --approver <name>\n"
            f"  python -m aventum_action.cli reject  {approval.approval_id} --approver <name>"
        )
    return EXIT_OK


def _decide(args, decision: str) -> int:
    config = load_config()
    factory = build_session_factory(build_engine(config.database_url))
    with session_scope(factory) as session:
        approval = session.get(Approval, args.approval_id)
        if approval is None:
            print(f"no approval {args.approval_id}")
            return EXIT_PRECONDITION
        try:
            decide_approval(
                session, approval, decision=decision,
                approver_identity=args.approver, note=args.note,
            )
        except Exception as exc:
            print(f"cannot decide: {exc}")
            return EXIT_PRECONDITION
        print(f"approval {approval.approval_id} -> {approval.status} by {args.approver}")
        if decision == "APPROVED":
            print(
                f"\nNEXT: execute (SIMULATED — no real infrastructure is contacted):\n"
                f"  python -m aventum_action.cli execute "
                f"{approval.recommendation_id} {approval.approval_id}"
            )
    return EXIT_OK


def cmd_approve(args) -> int:
    return _decide(args, "APPROVED")


def cmd_reject(args) -> int:
    return _decide(args, "REJECTED")


def cmd_execute(args) -> int:
    config = load_config()
    factory = build_session_factory(build_engine(config.database_url))
    with session_scope(factory) as session:
        from aventum_counterfactual.source import load_world_state

        rec = session.get(Recommendation, args.recommendation_id)
        if rec is None:
            print(f"no recommendation {args.recommendation_id}")
            return EXIT_PRECONDITION

        world = load_world_state(session, rec.incident_id)
        outcome = execute_action(
            session,
            recommendation_id=args.recommendation_id,
            approval_id=args.approval_id,
            world=world,
            alert_role=primary_alert_role(session, rec.analysis_run_id),
            executed_by=args.executed_by,
        )
        action = outcome.action
        print(f"\naction {action.action_id}: {action.status}")
        if outcome.duplicate:
            print("DUPLICATE SUPPRESSED — the adapter was not invoked again.")
        if action.rejection_reason:
            print(f"rejection reason : {action.rejection_reason}")
            for check in (action.revalidation_result or {}).get("checks", []):
                print(f"  [{check['result']}] {check['check']}")
            return EXIT_PRECONDITION

        print(f"adapter          : {action.adapter_name} (SIMULATED)")
        print("\nexpected_outcome (projected before the action):")
        print(_json(action.expected_outcome))
        print("\nactual_simulated_outcome (what the adapter modelled):")
        print(_json(action.actual_simulated_outcome))
        print(
            "\nNo recovery claim is made. Day 5 owns verification.\n"
            f"  python -m aventum_action.cli verify {action.action_id}"
        )
    return EXIT_OK


def cmd_status(args) -> int:
    config = load_config()
    factory = build_session_factory(build_engine(config.database_url))
    with session_scope(factory) as session:
        recs = session.scalars(
            select(Recommendation).order_by(Recommendation.recommendation_id.desc()).limit(20)
        ).all()
        print(f"\n{'rec':>5} {'incident':>9} {'action':<10} {'status':<18} {'policy':<10} "
              f"{'GMV retained':>14}")
        for r in recs:
            print(
                f"{r.recommendation_id:>5} {r.incident_id:>9} {r.action_type:<10} "
                f"{r.status:<18} {r.policy_validation_result:<10} "
                f"{float(r.expected_gmv_retained or 0):>14,.2f}"
            )
        approvals = session.scalars(
            select(Approval).order_by(Approval.approval_id.desc()).limit(20)
        ).all()
        print(f"\n{'appr':>5} {'rec':>5} {'status':<10} {'approver':<20} {'expires':<28}")
        for a in approvals:
            print(
                f"{a.approval_id:>5} {a.recommendation_id:>5} {a.status:<10} "
                f"{(a.approver_identity or '-'):<20} {a.expires_at.isoformat():<28}"
            )
        actions = session.scalars(select(Action).order_by(Action.action_id.desc()).limit(20)).all()
        print(f"\n{'act':>5} {'rec':>5} {'status':<12} {'adapter':<26} {'reason':<32}")
        for a in actions:
            print(
                f"{a.action_id:>5} {a.recommendation_id:>5} {a.status:<12} "
                f"{a.adapter_name:<26} {(a.rejection_reason or '-'):<32}"
            )
    return EXIT_OK


def cmd_audit(args) -> int:
    config = load_config()
    factory = build_session_factory(build_engine(config.database_url))
    with session_scope(factory) as session:
        events = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.incident_id == args.incident_id)
            .order_by(AuditEvent.event_id)
        ).all()
        print(f"\n{len(events)} audit events for incident {args.incident_id} (append-only)\n")
        print(f"{'id':>5} {'occurred_at':<28} {'event_type':<30} {'actor':<22}")
        for e in events:
            print(
                f"{e.event_id:>5} {e.occurred_at.isoformat():<28} {e.event_type:<30} {e.actor:<22}"
            )
    return EXIT_OK


def cmd_verify(args) -> int:
    config = load_config()
    factory = build_session_factory(build_engine(config.database_url))
    with session_scope(factory) as session:
        handoff = build_verification_handoff(session, args.action_id)
        print(_json(handoff.as_dict()))
        print("\n--- provenance chain ---")
        print(_json(provenance_chain(session, args.action_id)))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aventum Day 4A deterministic decision core.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("decide", help="Simulate candidates and build a recommendation.")
    p.add_argument("analysis_run_id", type=int)
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("payload", help="Print the human approval payload.")
    p.add_argument("recommendation_id", type=int)
    p.set_defaults(func=cmd_payload)

    p = sub.add_parser("request", help="Raise a pending approval for a human.")
    p.add_argument("recommendation_id", type=int)
    p.set_defaults(func=cmd_request)

    p = sub.add_parser("approve", help="HUMAN: approve a pending approval.")
    p.add_argument("approval_id", type=int)
    p.add_argument("--approver", required=True, help="Human identity. Mandatory.")
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="HUMAN: reject a pending approval.")
    p.add_argument("approval_id", type=int)
    p.add_argument("--approver", required=True, help="Human identity. Mandatory.")
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("execute", help="Execute an approved action (SIMULATED).")
    p.add_argument("recommendation_id", type=int)
    p.add_argument("approval_id", type=int)
    p.add_argument("--executed-by", default="operator")
    p.set_defaults(func=cmd_execute)

    p = sub.add_parser("status", help="List recommendations, approvals, and actions.")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("audit", help="Print the append-only audit trail for an incident.")
    p.add_argument("incident_id", type=int)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("verify", help="Print the Day 5 verification handoff as JSON.")
    p.add_argument("action_id", type=int)
    p.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # pragma: no cover - CLI surface
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
