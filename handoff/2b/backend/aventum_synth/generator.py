"""
Synthetic infrastructure generation orchestration (Day 2B).

Flow:
    canonical transactions (READ ONLY)
        -> seed/refresh gateway universe, profiles, policy, health windows
        -> per-transaction deterministic draw
        -> routing-policy eligibility -> gateway selection
        -> gateway runtime profile (baseline x health)
        -> coherent signal generation (response -> regime -> latency)
        -> bulk COPY into synthetic_infrastructure_assignments
        -> generation fingerprint + distribution report

Performance: one streamed read of `transactions`, one pass of pure-Python generation,
one COPY. No per-row queries, no per-row commits.
"""

from __future__ import annotations

import csv
import io
import json
import time
import tracemalloc
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, text

from . import GENERATION_CONFIG_VERSION, SYNTHETIC_MODEL_VERSION
from .calibration import (
    BASELINE_FAILURE_RESPONSE_MIX,
    CALIBRATION_REFERENCE_NAME,
    CALIBRATION_REFERENCE_NOTE,
    CALIBRATION_REFERENCE_VERSION,
    FAILURE_SPREAD_DAMPING,
    GATEWAY_CALIBRATION_SOURCE,
    GATEWAY_LATENCY_MULTIPLIER,
    GATEWAY_TRAFFIC_WEIGHT,
    LATENCY_REGIME_PARAMS,
    absolute_failure_probabilities,
    build_gateway_failure_profiles,
)
from .outcome_model import GatewayRuntimeProfile, generate_signals
from .rng import assignment_key, digest_for
from .routing import (
    ROUTING_POLICY_DESCRIPTION,
    ROUTING_POLICY_DISPLAY_NAME,
    ROUTING_POLICY_VERSION,
    SELECTION_METHOD,
    build_candidates,
    eligible_gateway_ids,
    eligible_gateway_record,
    select_gateway,
)

GATEWAY_PROFILE_VERSION = "baseline-v1"
DEFAULT_GENERATION_SEED = "aventum-day2b-baseline-001"

# Columns written by COPY, in a fixed order (determinism + reproducible fingerprint).
ASSIGNMENT_COLUMNS: tuple[str, ...] = (
    "transaction_id",
    "source_ingestion_run_id",
    "generation_run_id",
    "routing_policy_version",
    "eligible_gateways",
    "selected_gateway_id",
    "selection_method",
    "selection_seed",
    "gateway_profile_version",
    "gateway_health_state",
    "latency_regime",
    "gateway_latency_ms",
    "gateway_response_code",
    "response_attribution",
    "modeled_failure_probability",
)


class GenerationError(RuntimeError):
    """Generation aborted. Canonical data is untouched."""


class RunStatus:
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


@dataclass
class GenerationResult:
    generation_run_id: int | None
    status: str
    source_ingestion_run_id: int | None = None
    generation_seed: str = ""
    rows_generated: int = 0
    duration_seconds: float = 0.0
    rows_per_second: float = 0.0
    peak_memory_mb: float | None = None
    generation_fingerprint: str = ""
    observed_failure_rate: float = 0.0
    distribution_report: dict = field(default_factory=dict)
    message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == RunStatus.SUCCEEDED


# --------------------------------------------------------------------------
# Canonical context (read-only)
# --------------------------------------------------------------------------

def read_canonical_context(engine: Engine) -> dict:
    """Read the canonical facts the model needs. Never writes to Day 2A tables."""
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT COUNT(*)                                        AS total,
                       COUNT(DISTINCT ingestion_run_id)                AS run_count,
                       MIN(ingestion_run_id)                           AS ingestion_run_id,
                       SUM((status = 'FAILED')::int)::float / COUNT(*) AS failure_rate,
                       MIN(timestamp)                                  AS ts_min,
                       MAX(timestamp)                                  AS ts_max
                FROM transactions
                """
            )
        ).mappings().one()

    if not row["total"]:
        raise GenerationError(
            "No canonical transactions found. Run the Day 2A ingestion first:\n"
            "    python -m aventum_ingest.cli ingest"
        )
    if row["run_count"] != 1:
        raise GenerationError(
            f"Expected exactly one canonical ingestion run, found {row['run_count']}. "
            "Day 2B generates against a single canonical load."
        )
    return dict(row)


# --------------------------------------------------------------------------
# Configuration seeding (data-driven gateway behaviour)
# --------------------------------------------------------------------------

def seed_infrastructure_configuration(
    connection,
    failure_probabilities: dict[str, float],
) -> None:
    """
    Upsert the gateway universe, profiles, and routing policy.

    Behaviour lives in these tables rather than in generator code, so a later phase can
    add a profile version or scope eligibility without touching the generation logic.
    """
    profiles = build_gateway_failure_profiles()

    for gateway_id in sorted(GATEWAY_CALIBRATION_SOURCE):
        connection.execute(
            text(
                """
                INSERT INTO synthetic_gateways (
                    gateway_id, display_name, is_active, calibration_source_rail,
                    calibration_reference_name, notes
                ) VALUES (:gid, :name, true, :rail, :ref, :notes)
                ON CONFLICT (gateway_id) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    calibration_source_rail = EXCLUDED.calibration_source_rail,
                    calibration_reference_name = EXCLUDED.calibration_reference_name,
                    notes = EXCLUDED.notes
                """
            ),
            {
                "gid": gateway_id,
                "name": f"Aventum synthetic {gateway_id.replace('_', ' ').title()}",
                "rail": GATEWAY_CALIBRATION_SOURCE[gateway_id],
                "ref": CALIBRATION_REFERENCE_NAME,
                "notes": (
                    "Aventum synthetic model entity. Not a real payment gateway and not "
                    "affiliated with any payment processor. Relative profile informed by "
                    f"calibration rail {GATEWAY_CALIBRATION_SOURCE[gateway_id]}; no "
                    "reference row is imported or joined."
                ),
            },
        )

        profile = profiles[gateway_id]
        connection.execute(
            text(
                """
                INSERT INTO synthetic_gateway_profiles (
                    gateway_id, profile_version, baseline_traffic_weight,
                    relative_failure_multiplier, baseline_failure_probability,
                    latency_multiplier, failure_response_mix,
                    calibration_source_rail, calibration_notes
                ) VALUES (
                    :gid, :pver, :weight, :relmult, :failprob, :latmult,
                    CAST(:mix AS jsonb), :rail, :cnotes
                )
                ON CONFLICT (gateway_id, profile_version) DO UPDATE SET
                    baseline_traffic_weight = EXCLUDED.baseline_traffic_weight,
                    relative_failure_multiplier = EXCLUDED.relative_failure_multiplier,
                    baseline_failure_probability = EXCLUDED.baseline_failure_probability,
                    latency_multiplier = EXCLUDED.latency_multiplier,
                    failure_response_mix = EXCLUDED.failure_response_mix,
                    calibration_notes = EXCLUDED.calibration_notes
                """
            ),
            {
                "gid": gateway_id,
                "pver": GATEWAY_PROFILE_VERSION,
                "weight": profile.traffic_weight,
                "relmult": profile.relative_failure_multiplier,
                "failprob": round(failure_probabilities[gateway_id], 6),
                "latmult": GATEWAY_LATENCY_MULTIPLIER[gateway_id],
                "mix": json.dumps(BASELINE_FAILURE_RESPONSE_MIX[gateway_id]),
                "rail": profile.calibration_source_rail,
                "cnotes": (
                    f"Bounded transfer, damping={FAILURE_SPREAD_DAMPING}. "
                    "Relative spread from the calibration reference; absolute level "
                    "anchored to the OBSERVED canonical failure rate. "
                    + CALIBRATION_REFERENCE_NOTE
                ),
            },
        )

    connection.execute(
        text(
            """
            INSERT INTO synthetic_routing_policies (
                policy_version, display_name, description, selection_method, is_active
            ) VALUES (:pver, :name, :descr, :method, true)
            ON CONFLICT (policy_version) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                selection_method = EXCLUDED.selection_method
            """
        ),
        {
            "pver": ROUTING_POLICY_VERSION,
            "name": ROUTING_POLICY_DISPLAY_NAME,
            "descr": ROUTING_POLICY_DESCRIPTION,
            "method": SELECTION_METHOD,
        },
    )

    for gateway_id in sorted(GATEWAY_TRAFFIC_WEIGHT):
        connection.execute(
            text(
                """
                INSERT INTO synthetic_routing_policy_gateways (
                    policy_version, gateway_id, traffic_weight,
                    eligibility_conditions, is_eligible
                ) VALUES (:pver, :gid, :weight, NULL, true)
                ON CONFLICT (policy_version, gateway_id) DO UPDATE SET
                    traffic_weight = EXCLUDED.traffic_weight,
                    is_eligible = EXCLUDED.is_eligible
                """
            ),
            {
                "pver": ROUTING_POLICY_VERSION,
                "gid": gateway_id,
                "weight": GATEWAY_TRAFFIC_WEIGHT[gateway_id],
            },
        )


def seed_baseline_health(connection, generation_run_id: int, ts_min, ts_max) -> None:
    """
    Write one HEALTHY window per gateway covering the canonical period.

    Day 2B injects no degradation. Day 2C adds DEGRADED/UNAVAILABLE windows to this
    same table with multipliers > 1, and every downstream signal changes automatically
    through GatewayRuntimeProfile -- no schema change and no generator rewrite.

    The window is treated as half-open [valid_from, valid_to), so `valid_to` is padded
    one second past the last transaction. Without the pad, the final transaction would
    fall outside its own health window, and a canonical population spanning a single
    instant (ts_min == ts_max) would produce a zero-width window the CHECK rejects.
    """
    valid_to = ts_max + timedelta(seconds=1)

    for gateway_id in sorted(GATEWAY_CALIBRATION_SOURCE):
        connection.execute(
            text(
                """
                INSERT INTO synthetic_gateway_health_states (
                    gateway_id, generation_run_id, health_state, valid_from, valid_to,
                    failure_multiplier, latency_multiplier, timeout_multiplier, reason
                ) VALUES (
                    :gid, :grid, 'HEALTHY', :vfrom, :vto, 1.0, 1.0, 1.0,
                    'Day 2B baseline: normal operation, no degradation injected.'
                )
                """
            ),
            {"gid": gateway_id, "grid": generation_run_id, "vfrom": ts_min, "vto": valid_to},
        )


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def _build_runtime_profiles(
    failure_probabilities: dict[str, float],
) -> dict[str, GatewayRuntimeProfile]:
    """Baseline runtime profiles: all HEALTHY, all multipliers 1.0."""
    return {
        gateway_id: GatewayRuntimeProfile(
            gateway_id=gateway_id,
            profile_version=GATEWAY_PROFILE_VERSION,
            baseline_failure_probability=failure_probabilities[gateway_id],
            latency_multiplier=GATEWAY_LATENCY_MULTIPLIER[gateway_id],
            failure_response_mix=BASELINE_FAILURE_RESPONSE_MIX[gateway_id],
            health_state="HEALTHY",
            failure_multiplier=1.0,
            health_latency_multiplier=1.0,
            timeout_multiplier=1.0,
        )
        for gateway_id in failure_probabilities
    }


def _iter_canonical_transactions(connection):
    """
    Stream canonical transactions in a deterministic order.

    ORDER BY transaction_id makes the read reproducible; the generation itself does not
    depend on order (each row's values come from its own hash), but a stable order keeps
    the COPY payload and therefore the fingerprint byte-identical between runs.
    """
    result = connection.execution_options(stream_results=True, yield_per=10_000).execute(
        text("SELECT transaction_id, status FROM transactions ORDER BY transaction_id")
    )
    for row in result:
        yield row[0], row[1]


def generate_assignments(
    engine: Engine,
    generation_run_id: int,
    source_ingestion_run_id: int,
    generation_seed: str,
    failure_probabilities: dict[str, float],
    policy_gateways: list[dict],
    batch_rows: int = 20_000,
) -> tuple[int, dict]:
    """
    Generate and bulk-load every synthetic assignment.

    Streams: canonical rows are read with a server-side cursor and written into an open
    COPY in fixed-size batches, so peak memory stays flat regardless of dataset size
    rather than materialising the whole payload. One COPY, one transaction, no per-row
    INSERT and no per-row commit.

    Returns (rows_written, distribution_report).
    """
    candidates = build_candidates(policy_gateways, failure_probabilities)
    runtime_profiles = _build_runtime_profiles(failure_probabilities)
    # Compact per-row eligibility; the full reasoned snapshot lives on the run row.
    eligible_json = json.dumps(eligible_gateway_ids(candidates))

    counts = {
        "gateway": {},
        "response": {},
        "latency_regime": {},
        "health": {},
        "gateway_failed": {},
        "gateway_total": {},
    }
    latency_sum = 0.0
    latency_min = float("inf")
    latency_max = 0.0
    rows = 0

    columns = ", ".join(ASSIGNMENT_COLUMNS)
    copy_sql = (
        f"COPY synthetic_infrastructure_assignments ({columns}) "
        "FROM STDIN WITH (FORMAT csv, NULL '')"
    )

    # Separate connections: one streams the read, one owns the COPY.
    with engine.connect() as read_connection, engine.begin() as write_connection:
        raw_connection = write_connection.connection
        with raw_connection.cursor() as cursor, cursor.copy(copy_sql) as copy:
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            batch_count = 0

            for transaction_id, observed_status in _iter_canonical_transactions(read_connection):
                digest = digest_for(
                    transaction_id,
                    source_ingestion_run_id,
                    GENERATION_CONFIG_VERSION,
                    generation_seed,
                )
                gateway_id = select_gateway(digest, observed_status, candidates)
                profile = runtime_profiles[gateway_id]
                signals = generate_signals(digest, observed_status, profile)

                writer.writerow([
                    transaction_id,
                    source_ingestion_run_id,
                    generation_run_id,
                    ROUTING_POLICY_VERSION,
                    eligible_json,
                    gateway_id,
                    SELECTION_METHOD,
                    assignment_key(
                        transaction_id,
                        source_ingestion_run_id,
                        GENERATION_CONFIG_VERSION,
                        generation_seed,
                    ),
                    signals["gateway_profile_version"],
                    signals["gateway_health_state"],
                    signals["latency_regime"],
                    signals["gateway_latency_ms"],
                    signals["gateway_response_code"],
                    signals["response_attribution"],
                    signals["modeled_failure_probability"],
                ])

                rows += 1
                batch_count += 1
                counts["gateway"][gateway_id] = counts["gateway"].get(gateway_id, 0) + 1
                counts["gateway_total"][gateway_id] = (
                    counts["gateway_total"].get(gateway_id, 0) + 1
                )
                if observed_status == "FAILED":
                    counts["gateway_failed"][gateway_id] = (
                        counts["gateway_failed"].get(gateway_id, 0) + 1
                    )
                rc = signals["gateway_response_code"]
                counts["response"][rc] = counts["response"].get(rc, 0) + 1
                lr = signals["latency_regime"]
                counts["latency_regime"][lr] = counts["latency_regime"].get(lr, 0) + 1
                hs = signals["gateway_health_state"]
                counts["health"][hs] = counts["health"].get(hs, 0) + 1

                latency = float(signals["gateway_latency_ms"])
                latency_sum += latency
                latency_min = min(latency_min, latency)
                latency_max = max(latency_max, latency)

                if batch_count >= batch_rows:
                    copy.write(buffer.getvalue())
                    buffer.seek(0)
                    buffer.truncate(0)
                    batch_count = 0

            if batch_count:
                copy.write(buffer.getvalue())

    report = {
        "rows": rows,
        "gateway_distribution": dict(sorted(counts["gateway"].items())),
        "gateway_failure_rate_pct": {
            gid: round(
                100.0 * counts["gateway_failed"].get(gid, 0) / counts["gateway_total"][gid], 4
            )
            for gid in sorted(counts["gateway_total"])
        },
        "response_distribution": dict(sorted(counts["response"].items())),
        "latency_regime_distribution": dict(sorted(counts["latency_regime"].items())),
        "health_distribution": dict(sorted(counts["health"].items())),
        "latency_ms": {
            "min": round(latency_min, 2),
            "mean": round(latency_sum / rows, 2) if rows else 0.0,
            "max": round(latency_max, 2),
        },
    }
    return rows, report


def compute_generation_fingerprint(engine: Engine, generation_run_id: int) -> str:
    """
    Deterministic SHA-256 over the generated synthetic population.

    Depends on the generation inputs (source ingestion run, config version, seed, model
    version -- all embedded in the run row and in every selection_seed) and on the
    ordered assignment content. Ordered by transaction_id so it is independent of
    physical row order. Computed server-side to avoid streaming 250k rows back.
    """
    sql = text(
        """
        SELECT encode(sha256(convert_to(string_agg(row_repr, E'\\n' ORDER BY transaction_id),
                                        'UTF8')), 'hex')
        FROM (
            SELECT
                a.transaction_id,
                a.transaction_id
                || '|' || a.source_ingestion_run_id
                || '|' || a.routing_policy_version
                || '|' || a.selected_gateway_id
                || '|' || a.selection_method
                || '|' || a.gateway_profile_version
                || '|' || a.gateway_health_state
                || '|' || a.latency_regime
                || '|' || to_char(a.gateway_latency_ms, 'FM9999999990.00')
                || '|' || a.gateway_response_code
                || '|' || a.response_attribution
                || '|' || to_char(a.modeled_failure_probability, 'FM0.000000')
                || '|' || r.generation_seed
                || '|' || r.generation_config_version
                || '|' || r.synthetic_model_version
                AS row_repr
            FROM synthetic_infrastructure_assignments a
            JOIN synthetic_generation_runs r
              ON r.generation_run_id = a.generation_run_id
            WHERE a.generation_run_id = :grid
        ) AS rows
        """
    )
    with engine.connect() as connection:
        return connection.execute(sql, {"grid": generation_run_id}).scalar_one()


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run_generation(
    engine: Engine,
    generation_seed: str = DEFAULT_GENERATION_SEED,
    measure_memory: bool = False,
) -> GenerationResult:
    """
    Execute a full synthetic infrastructure generation.

    Any prior generation for the same canonical ingestion run is marked SUPERSEDED and
    its assignments removed, so the synthetic population is always a single coherent set
    rather than an accumulation of overlapping generations.
    """
    started = time.perf_counter()
    if measure_memory:
        tracemalloc.start()

    context = read_canonical_context(engine)
    source_ingestion_run_id = int(context["ingestion_run_id"])
    observed_failure_rate = float(context["failure_rate"])

    # Absolute failure probabilities are anchored to the OBSERVED rate, so the
    # calibration reference never sets the level -- only the relative spread.
    failure_probabilities = absolute_failure_probabilities(observed_failure_rate)

    # Build the eligibility snapshot once so it can be recorded on the run row.
    eligibility_snapshot = eligible_gateway_record(
        build_candidates(
            [
                {
                    "gateway_id": gid,
                    "traffic_weight": GATEWAY_TRAFFIC_WEIGHT[gid],
                    "eligibility_conditions": None,
                    "is_eligible": True,
                }
                for gid in sorted(GATEWAY_TRAFFIC_WEIGHT)
            ],
            failure_probabilities,
        )
    )

    model_parameters = {
        "gateway_traffic_weights": GATEWAY_TRAFFIC_WEIGHT,
        "gateway_failure_probabilities": {
            k: round(v, 6) for k, v in sorted(failure_probabilities.items())
        },
        "gateway_latency_multipliers": GATEWAY_LATENCY_MULTIPLIER,
        "latency_regime_params": LATENCY_REGIME_PARAMS,
        "failure_spread_damping": FAILURE_SPREAD_DAMPING,
        "observed_failure_rate": round(observed_failure_rate, 6),
        "calibration_reference": {
            "name": CALIBRATION_REFERENCE_NAME,
            "version": CALIBRATION_REFERENCE_VERSION,
            "note": CALIBRATION_REFERENCE_NOTE,
        },
        # Full reasoned eligibility snapshot, stored ONCE here rather than duplicated
        # onto every assignment row. Answers "why was this gateway eligible?" for the
        # whole run; the per-row column carries the compact eligible-ID list.
        "routing_eligibility_snapshot": eligibility_snapshot,
    }

    with engine.begin() as connection:
        # Retire any earlier generation for this canonical load.
        connection.execute(
            text(
                """
                UPDATE synthetic_generation_runs
                SET status = :superseded
                WHERE status = :succeeded AND source_ingestion_run_id = :sirid
                """
            ),
            {
                "superseded": RunStatus.SUPERSEDED,
                "succeeded": RunStatus.SUCCEEDED,
                "sirid": source_ingestion_run_id,
            },
        )
        connection.execute(text("DELETE FROM synthetic_infrastructure_assignments"))

        seed_infrastructure_configuration(connection, failure_probabilities)

        generation_run_id = connection.execute(
            text(
                """
                INSERT INTO synthetic_generation_runs (
                    source_ingestion_run_id, generation_seed, generation_config_version,
                    synthetic_model_version, routing_policy_version,
                    calibration_reference_name, calibration_reference_version,
                    status, observed_failure_rate, model_parameters
                ) VALUES (
                    :sirid, :seed, :cfgver, :modelver, :polver, :calname, :calver,
                    :status, :failrate, CAST(:params AS jsonb)
                )
                RETURNING generation_run_id
                """
            ),
            {
                "sirid": source_ingestion_run_id,
                "seed": generation_seed,
                "cfgver": GENERATION_CONFIG_VERSION,
                "modelver": SYNTHETIC_MODEL_VERSION,
                "polver": ROUTING_POLICY_VERSION,
                "calname": CALIBRATION_REFERENCE_NAME,
                "calver": CALIBRATION_REFERENCE_VERSION,
                "status": RunStatus.RUNNING,
                "failrate": round(observed_failure_rate, 6),
                "params": json.dumps(model_parameters, sort_keys=True, default=str),
            },
        ).scalar_one()

        seed_baseline_health(
            connection, generation_run_id, context["ts_min"], context["ts_max"]
        )

        policy_gateways = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT gateway_id, traffic_weight, eligibility_conditions, is_eligible
                    FROM synthetic_routing_policy_gateways
                    WHERE policy_version = :pver
                    ORDER BY gateway_id
                    """
                ),
                {"pver": ROUTING_POLICY_VERSION},
            ).mappings()
        ]

    try:
        rows, report = generate_assignments(
            engine,
            generation_run_id=generation_run_id,
            source_ingestion_run_id=source_ingestion_run_id,
            generation_seed=generation_seed,
            failure_probabilities=failure_probabilities,
            policy_gateways=policy_gateways,
        )

        fingerprint = compute_generation_fingerprint(engine, generation_run_id)
        duration = time.perf_counter() - started

        peak_mb = None
        if measure_memory:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peak_mb = round(peak / (1024 * 1024), 2)

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE synthetic_generation_runs SET
                        status = :status,
                        finished_at = :finished_at,
                        duration_seconds = :duration,
                        rows_generated = :rows,
                        generation_fingerprint = :fingerprint,
                        distribution_report = CAST(:report AS jsonb)
                    WHERE generation_run_id = :grid
                    """
                ),
                {
                    "status": RunStatus.SUCCEEDED,
                    # Wall-clock is audit metadata only, never a generation input.
                    "finished_at": datetime.now(timezone.utc),
                    "duration": round(duration, 3),
                    "rows": rows,
                    "fingerprint": fingerprint,
                    "report": json.dumps(report, sort_keys=True),
                    "grid": generation_run_id,
                },
            )

        return GenerationResult(
            generation_run_id=generation_run_id,
            status=RunStatus.SUCCEEDED,
            source_ingestion_run_id=source_ingestion_run_id,
            generation_seed=generation_seed,
            rows_generated=rows,
            duration_seconds=duration,
            rows_per_second=round(rows / duration, 1) if duration else 0.0,
            peak_memory_mb=peak_mb,
            generation_fingerprint=fingerprint,
            observed_failure_rate=observed_failure_rate,
            distribution_report=report,
            message="Synthetic infrastructure baseline generated.",
        )

    except Exception as exc:
        if measure_memory and tracemalloc.is_tracing():
            tracemalloc.stop()
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE synthetic_generation_runs SET
                            status = :status, finished_at = now(),
                            duration_seconds = :duration, error_message = :error
                        WHERE generation_run_id = :grid
                        """
                    ),
                    {
                        "status": RunStatus.FAILED,
                        "duration": round(time.perf_counter() - started, 3),
                        "error": f"{type(exc).__name__}: {exc}",
                        "grid": generation_run_id,
                    },
                )
        except Exception:
            pass  # never mask the original failure
        raise
