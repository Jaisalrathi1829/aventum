"""
Day 3 command-line interface.

    python -m aventum_incident.cli golden        # inject + simulate + detect + RCA
    python -m aventum_incident.cli quiet         # no-incident control scan
    python -m aventum_incident.cli alternative   # issuer-centred scenario
    python -m aventum_incident.cli scenarios     # run all three and compare
    python -m aventum_incident.cli status        # incidents and analysis runs
    python -m aventum_incident.cli handoff <id>  # print the Day 4 handoff as JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from sqlalchemy import select

from aventum_ingest.config import load_config
from aventum_ingest.db import build_engine, build_session_factory, session_scope

from .evaluation import evaluate_rca
from .handoff import build_handoff, ranked_hypotheses
from .models import Incident, IncidentAnalysisRun
from .pipeline import PipelineResult, run_incident_pipeline, run_quiet_analysis
from .scenarios import active_generation_run, alternative_incident, golden_incident, quiet_window

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PRECONDITION = 4


def _report(result: PipelineResult, title: str) -> None:
    print(f"\n=== {title} ===")
    if result.incident is not None:
        print(
            f"Incident      : {result.incident.incident_id} "
            f"({result.incident.incident_name}, status={result.incident.status})"
        )
    if result.simulation is not None:
        sim = result.simulation
        print(
            f"Simulated     : {sim.rows_simulated:,} rows in window, "
            f"{sim.rows_changed:,} changed"
        )
        print(
            f"  affected    : n={sim.affected_population:,} "
            f"failure_rate={sim.affected_failure_rate:.4%}"
        )
        print(
            f"  control     : n={sim.control_population:,} "
            f"failure_rate={sim.control_failure_rate:.4%}"
        )
        print(f"  fingerprint : {sim.simulation_fingerprint}")

    print(f"Analysis run  : {result.analysis_run_id}")
    print(
        f"Detection     : {len(result.detection.reported)} reported "
        f"({len(result.detection.candidates)} scored, "
        f"{result.detection.cohorts_scanned} cohorts scanned)"
    )
    for candidate in result.detection.reported[:5]:
        print(
            f"  #{candidate.rank} {candidate.cohort_key:<45} "
            f"{candidate.baseline.failure_rate:.3%} -> {candidate.current.failure_rate:.3%} "
            f"({candidate.significance_sigma:6.2f} sigma, {candidate.severity})"
        )

    print(f"Evidence      : {len(result.evidence.records)} records")
    print("Hypotheses    :")
    for hypothesis in result.hypotheses:
        print(
            f"  #{hypothesis.rank} {hypothesis.hypothesis_type:<32} "
            f"score={hypothesis.score:.4f} confidence={hypothesis.confidence:.4f} "
            f"(+{len(hypothesis.supporting_evidence_ids)}/"
            f"-{len(hypothesis.contradicting_evidence_ids)})"
        )

    rca = result.rca
    print(f"RCA verdict   : {rca.verdict}  confidence={rca.confidence:.4f}")
    print(f"RCA cause     : {rca.predicted_root_cause}")
    print(f"RCA gateway   : {rca.predicted_gateway_id}")
    print(f"Fingerprint   : {result.analysis_fingerprint}")
    print(
        f"Timing        : detect={result.detection.elapsed_ms:.1f}ms "
        f"evidence={result.evidence.elapsed_ms:.1f}ms rca={rca.elapsed_ms:.1f}ms "
        f"total={result.total_ms:.1f}ms"
    )


def cmd_golden(args: argparse.Namespace) -> int:
    config = load_config()
    engine = build_engine(config.database_url)
    factory = build_session_factory(engine)
    with session_scope(factory) as session:
        definition = golden_incident(session, seed=args.seed)
        result = run_incident_pipeline(session, definition)
        _report(result, "SCENARIO A - GOLDEN (gateway_C degradation)")

        evaluation = evaluate_rca(
            session,
            result.incident.incident_id,
            result.rca,
            expected_hypothesis_type="gateway_degradation",
        )
        print("\n--- evaluation (ground truth consulted only now) ---")
        print(f"  ground truth : {evaluation.ground_truth_root_cause}")
        print(f"  predicted    : {evaluation.predicted_root_cause}")
        print(f"  correct      : {evaluation.correct}")
    return EXIT_OK


def cmd_alternative(args: argparse.Namespace) -> int:
    config = load_config()
    engine = build_engine(config.database_url)
    factory = build_session_factory(engine)
    with session_scope(factory) as session:
        definition = alternative_incident(session, seed=args.seed)
        result = run_incident_pipeline(session, definition)
        _report(result, "SCENARIO C - ALTERNATIVE (issuer degradation)")

        evaluation = evaluate_rca(
            session,
            result.incident.incident_id,
            result.rca,
            expected_hypothesis_type="issuer_degradation",
        )
        print("\n--- evaluation (ground truth consulted only now) ---")
        print(f"  ground truth : {evaluation.ground_truth_root_cause}")
        print(f"  predicted    : {evaluation.predicted_root_cause}")
        print(f"  correct      : {evaluation.correct}")
        print(f"  blamed gateway_C: {evaluation.predicted_gateway_id == 'gateway_C'}")
    return EXIT_OK


def cmd_quiet(args: argparse.Namespace) -> int:
    config = load_config()
    engine = build_engine(config.database_url)
    factory = build_session_factory(engine)
    with session_scope(factory) as session:
        generation_run_id, _ = active_generation_run(session)
        window = quiet_window()
        result = run_quiet_analysis(session, generation_run_id, window)
        _report(result, "SCENARIO B - NO INCIDENT (false-positive control)")
        high = [c for c in result.detection.reported if c.severity in ("CRITICAL", "HIGH")]
        print(f"\n  high-severity alerts: {len(high)} (expected 0)")
    return EXIT_OK


GOLDEN_SEED = "aventum-day3-golden-001"
ALTERNATIVE_SEED = "aventum-day3-alternative-001"


def cmd_scenarios(args: argparse.Namespace) -> int:
    """
    Run all three scenarios, each with ITS OWN seed.

    Passing one shared `--seed` through to every scenario would silently run the
    alternative incident under the golden scenario's seed, producing a different (still
    deterministic, but unintended) simulation than `cli alternative` does on its own.
    Each scenario therefore gets its own namespace.
    """
    cmd_golden(argparse.Namespace(seed=GOLDEN_SEED))
    cmd_quiet(argparse.Namespace(seed="unused"))
    cmd_alternative(argparse.Namespace(seed=ALTERNATIVE_SEED))
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config()
    engine = build_engine(config.database_url)
    factory = build_session_factory(engine)
    with session_scope(factory) as session:
        incidents = session.scalars(select(Incident).order_by(Incident.incident_id)).all()
        print(f"{'id':>4}  {'name':<44} {'type':<28} {'status':<10} window")
        for incident in incidents:
            print(
                f"{incident.incident_id:>4}  {incident.incident_name:<44} "
                f"{incident.incident_type:<28} {incident.status:<10} "
                f"{incident.incident_start:%Y-%m-%d} -> {incident.incident_end:%Y-%m-%d}"
            )

        runs = session.scalars(
            select(IncidentAnalysisRun).order_by(IncidentAnalysisRun.analysis_run_id)
        ).all()
        print(f"\n{'run':>4}  {'incident':>8}  {'status':<10} {'anomalies':>9}  fingerprint")
        for run in runs:
            print(
                f"{run.analysis_run_id:>4}  {str(run.incident_id or '-'):>8}  "
                f"{run.status:<10} {run.anomalies_found:>9}  "
                f"{(run.analysis_fingerprint or '')[:16]}"
            )
    return EXIT_OK


def cmd_handoff(args: argparse.Namespace) -> int:
    config = load_config()
    engine = build_engine(config.database_url)
    factory = build_session_factory(engine)
    with session_scope(factory) as session:
        handoff = build_handoff(session, args.analysis_run_id)
        payload = handoff.as_dict()
        payload["hypotheses"] = ranked_hypotheses(session, args.analysis_run_id)
        print(json.dumps(payload, indent=2, default=str))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aventum_incident.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_golden = sub.add_parser("golden", help="Run the flagship gateway_C scenario.")
    p_golden.add_argument("--seed", default="aventum-day3-golden-001")
    p_golden.set_defaults(func=cmd_golden)

    p_alt = sub.add_parser("alternative", help="Run the issuer-degradation scenario.")
    p_alt.add_argument("--seed", default="aventum-day3-alternative-001")
    p_alt.set_defaults(func=cmd_alternative)

    p_quiet = sub.add_parser("quiet", help="Scan a window with no injected incident.")
    p_quiet.add_argument("--seed", default="unused")
    p_quiet.set_defaults(func=cmd_quiet)

    p_all = sub.add_parser("scenarios", help="Run all three scenarios.")
    p_all.add_argument("--seed", default="aventum-day3-golden-001")
    p_all.set_defaults(func=cmd_scenarios)

    p_status = sub.add_parser("status", help="List incidents and analysis runs.")
    p_status.set_defaults(func=cmd_status)

    p_handoff = sub.add_parser("handoff", help="Print the Day 4 handoff object as JSON.")
    p_handoff.add_argument("analysis_run_id", type=int)
    p_handoff.set_defaults(func=cmd_handoff)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"precondition not met: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION


if __name__ == "__main__":
    raise SystemExit(main())
