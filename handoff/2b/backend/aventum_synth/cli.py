"""
Day 2B command-line entrypoints.

    python -m aventum_synth.cli generate [--seed SEED] [--measure-memory]
    python -m aventum_synth.cli verify
    python -m aventum_synth.cli status
    python -m aventum_synth.cli cohorts [--limit N]
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from aventum_ingest.config import load_config
from aventum_ingest.db import build_engine, database_is_reachable, table_exists

from .generator import DEFAULT_GENERATION_SEED, GenerationError, run_generation
from .verify import StalenessState, assess_staleness, cohort_volumes, verify_generation


def _require_ready(engine) -> None:
    if not database_is_reachable(engine):
        raise SystemExit(
            "Cannot reach the database. Start it with:\n"
            "    cd backend && docker compose up -d"
        )
    if not table_exists(engine, "synthetic_infrastructure_assignments"):
        raise SystemExit(
            "Day 2B schema is not migrated. Run:\n"
            "    cd backend && .venv/Scripts/python -m alembic upgrade head"
        )


def cmd_generate(args: argparse.Namespace) -> int:
    config = load_config()
    engine = build_engine(config.database_url)
    _require_ready(engine)

    print(f"Target : {config.database_url.rsplit('@', 1)[-1]}")
    print(f"Seed   : {args.seed}")
    print("-" * 72)

    try:
        result = run_generation(
            engine, generation_seed=args.seed, measure_memory=args.measure_memory
        )
    except GenerationError as exc:
        print(f"GENERATION FAILURE: {exc}", file=sys.stderr)
        return 2

    report = result.distribution_report
    print(f"Generation run id : {result.generation_run_id}")
    print(f"Status            : {result.status}")
    print(f"Source ingestion  : run {result.source_ingestion_run_id}")
    print(f"Rows generated    : {result.rows_generated:,}")
    print(f"Observed fail rate: {result.observed_failure_rate * 100:.4f}%  (from canonical data)")
    print(f"Duration          : {result.duration_seconds:.2f}s")
    print(f"Throughput        : {result.rows_per_second:,.0f} rows/sec")
    if result.peak_memory_mb is not None:
        print(f"Peak memory       : {result.peak_memory_mb:.1f} MB")
    print(f"Fingerprint       : {result.generation_fingerprint}")
    print()
    print(f"Gateway traffic   : {report.get('gateway_distribution')}")
    print(f"Gateway fail %    : {report.get('gateway_failure_rate_pct')}")
    print(f"Response mix      : {report.get('response_distribution')}")
    print(f"Latency regimes   : {report.get('latency_regime_distribution')}")
    print(f"Health states     : {report.get('health_distribution')}")
    print(f"Latency ms        : {report.get('latency_ms')}")
    return 0 if result.succeeded else 1


def cmd_verify(args: argparse.Namespace) -> int:
    config = load_config()
    engine = build_engine(config.database_url)
    _require_ready(engine)

    staleness = assess_staleness(engine)
    print(f"Staleness : {staleness['state']}")
    print(f"            {staleness['detail']}")
    if staleness["state"] == StalenessState.ABSENT:
        return 1

    report = verify_generation(engine, staleness["generation_run_id"])
    print()
    print(report.summary())
    for check in report.checks:
        print(f"  [{'PASS' if check.passed else 'FAIL'}] {check.name}")
    return 0 if report.passed and staleness["state"] == StalenessState.CURRENT else 1


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config()
    engine = build_engine(config.database_url)
    _require_ready(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT generation_run_id, status, source_ingestion_run_id, rows_generated,
                       duration_seconds, generation_seed, generation_fingerprint
                FROM synthetic_generation_runs
                ORDER BY generation_run_id DESC LIMIT 20
                """
            )
        ).mappings().all()

    if not rows:
        print("No synthetic generation runs recorded.")
        return 0

    print(f"{'id':>3}  {'status':<12} {'ingest':>6} {'rows':>9}  fingerprint")
    for r in rows:
        fp = (r["generation_fingerprint"] or "")[:16]
        print(
            f"{r['generation_run_id']:>3}  {r['status']:<12} "
            f"{r['source_ingestion_run_id']:>6} {r['rows_generated']:>9,}  {fp}..."
        )
    return 0


def cmd_cohorts(args: argparse.Namespace) -> int:
    config = load_config()
    engine = build_engine(config.database_url)
    _require_ready(engine)

    cohorts = cohort_volumes(engine, limit=args.limit)
    if not cohorts:
        print("No cohorts found -- generate the synthetic baseline first.")
        return 1

    print("Baseline cohort volumes (gateway x sender_bank x payment_method)")
    print(f"{'gateway':<12} {'bank':<10} {'method':<14} {'volume':>8}  baseline_fail%")
    for c in cohorts:
        print(
            f"{c['gateway']:<12} {c['sender_bank']:<10} {c['payment_method']:<14} "
            f"{c['volume']:>8,}  {c['baseline_failure_rate_pct']:>6.3f}%"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aventum_synth",
        description="Aventum synthetic payment-infrastructure baseline (Day 2B).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="Generate the synthetic infrastructure baseline.")
    p_gen.add_argument("--seed", default=DEFAULT_GENERATION_SEED, help="Generation seed.")
    p_gen.add_argument("--measure-memory", action="store_true",
                       help="Track peak Python heap during generation.")
    p_gen.set_defaults(func=cmd_generate)

    p_ver = sub.add_parser("verify", help="Verify the synthetic population and staleness.")
    p_ver.set_defaults(func=cmd_verify)

    p_stat = sub.add_parser("status", help="List recent generation runs.")
    p_stat.set_defaults(func=cmd_status)

    p_coh = sub.add_parser("cohorts", help="Report baseline cohort volumes.")
    p_coh.add_argument("--limit", type=int, default=15)
    p_coh.set_defaults(func=cmd_cohorts)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
