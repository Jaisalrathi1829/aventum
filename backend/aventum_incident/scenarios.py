"""
Named incident scenarios.

Three scenarios exist, and the second and third are not optional extras -- they are what
distinguishes a working detector from one overfitted to a single demo:

  A. GOLDEN     -- the flagship gateway_C degradation. RCA should name gateway_C.
  B. NO INCIDENT-- an ordinary window with nothing injected. The detector must stay
                   quiet; a system that finds a critical incident in normal traffic is
                   worse than useless.
  C. ALTERNATIVE-- an issuer-centred degradation on a different dimension entirely.
                   RCA must NOT reflexively blame a gateway. This is the scenario that
                   proves the engine reasons from evidence rather than from a hard-coded
                   favourite answer.

Window arithmetic is done in IST because Day 1 established the canonical dataset's
timestamps as IST-local (docs/DATA_DICTIONARY.md); the values stored and compared are
timezone-aware UTC instants either way.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from .incident import IncidentDefinition

IST = timezone(timedelta(hours=5, minutes=30))

# The flagship window, chosen in the Day 2B review from measured cohort density
# (docs/DAY2B_ARCHITECTURE_REVIEW.md, Flagship Cohort Readiness).
GOLDEN_WINDOW_START = datetime(2024, 6, 1, 0, 0, 0, tzinfo=IST)
GOLDEN_WINDOW_END = datetime(2024, 6, 4, 0, 0, 0, tzinfo=IST)

# Calibration. docs/DAY2C_INTERFACE_READINESS.md §7 derived ~3.1-3.9x as the multiplier
# range that takes gateway_C's ~6.4% baseline into the 20-25% target band over a 3-day
# window. 3.5 sits mid-range; the acceptance test verifies the realised rate empirically
# rather than trusting this number.
GOLDEN_FAILURE_MULTIPLIER = 3.5
GOLDEN_LATENCY_MULTIPLIER = 2.2
# A degrading gateway should not merely fail more, it should fail DIFFERENTLY -- shifting
# toward infrastructure-side responses. This tilt is what lets the hypothesis engine
# tell an infrastructure fault from an issuer fault.
GOLDEN_TIMEOUT_MULTIPLIER = 6.0
GOLDEN_TARGET_FAILURE_RATE = 0.225

# An issuer problem is not an infrastructure problem: its failures stay in the
# issuer-side response families, so the timeout tilt is left at 1.0. That difference is
# a genuine diagnostic signal, not a thumb on the scale.
ALTERNATIVE_FAILURE_MULTIPLIER = 4.5
ALTERNATIVE_LATENCY_MULTIPLIER = 1.1
ALTERNATIVE_TIMEOUT_MULTIPLIER = 1.0
ALTERNATIVE_TARGET_FAILURE_RATE = 0.22

GOLDEN_INCIDENT_NAME = "golden-gateway-c-degradation"
ALTERNATIVE_INCIDENT_NAME = "alternative-issuer-degradation"

_ACTIVE_RUN_SQL = text(
    """
    SELECT generation_run_id, source_ingestion_run_id
    FROM synthetic_generation_runs
    WHERE status = 'SUCCEEDED'
    ORDER BY generation_run_id DESC
    LIMIT 1
    """
)

_LARGEST_BANK_SQL = text(
    """
    SELECT t.sender_bank
    FROM transactions t
    WHERE t.timestamp >= :window_start AND t.timestamp < :window_end
    GROUP BY t.sender_bank
    ORDER BY count(*) DESC, t.sender_bank ASC
    LIMIT 1
    """
)


def active_generation_run(session: Session) -> tuple[int, int]:
    """The current synthetic baseline every Day 3 scenario is built against."""
    row = session.execute(_ACTIVE_RUN_SQL).mappings().first()
    if row is None:
        raise RuntimeError(
            "no SUCCEEDED synthetic generation run found; "
            "run `python -m aventum_synth.cli generate` first"
        )
    return int(row["generation_run_id"]), int(row["source_ingestion_run_id"])


def golden_incident(
    session: Session,
    seed: str = "aventum-day3-golden-001",
    window: tuple[datetime, datetime] | None = None,
) -> IncidentDefinition:
    """Scenario A -- the flagship gateway_C degradation."""
    generation_run_id, ingestion_run_id = active_generation_run(session)
    start, end = window or (GOLDEN_WINDOW_START, GOLDEN_WINDOW_END)
    return IncidentDefinition(
        incident_name=GOLDEN_INCIDENT_NAME,
        incident_type="gateway_degradation",
        affected_gateway_id="gateway_C",
        affected_segment=None,
        incident_start=start,
        incident_end=end,
        failure_multiplier=GOLDEN_FAILURE_MULTIPLIER,
        latency_multiplier=GOLDEN_LATENCY_MULTIPLIER,
        timeout_multiplier=GOLDEN_TIMEOUT_MULTIPLIER,
        target_failure_rate=GOLDEN_TARGET_FAILURE_RATE,
        generation_run_id=generation_run_id,
        source_ingestion_run_id=ingestion_run_id,
        incident_seed=seed,
        ground_truth_root_cause="Synthetic degradation injected into gateway_C",
        ground_truth_detail={
            "mechanism": "failure/latency/timeout multipliers applied to gateway_C",
            "scenario": "A-golden",
            "note": "EVALUATION ONLY -- never an input to detection or RCA",
        },
        notes=(
            "Flagship scenario. Synthetic incident on a synthetic gateway; it did not "
            "occur in historical production and must never be presented as if it did."
        ),
    )


def alternative_incident(
    session: Session,
    seed: str = "aventum-day3-alternative-001",
    window: tuple[datetime, datetime] | None = None,
    sender_bank: str | None = None,
) -> IncidentDefinition:
    """
    Scenario C -- an issuer-centred degradation, spread across every gateway.

    The affected bank is resolved from the data (largest in the window) rather than
    hard-coded, so this scenario is a genuine test of the detector rather than a second
    memorised answer.
    """
    generation_run_id, ingestion_run_id = active_generation_run(session)
    start, end = window or (GOLDEN_WINDOW_START, GOLDEN_WINDOW_END)

    if sender_bank is None:
        row = session.execute(
            _LARGEST_BANK_SQL, {"window_start": start, "window_end": end}
        ).mappings().first()
        if row is None:
            raise RuntimeError("no transactions in the requested window")
        sender_bank = row["sender_bank"]

    return IncidentDefinition(
        incident_name=f"{ALTERNATIVE_INCIDENT_NAME}-{sender_bank}",
        incident_type="issuer_degradation",
        # No affected gateway: the degradation follows the issuer across all of them.
        affected_gateway_id=None,
        affected_segment={"sender_bank": sender_bank},
        incident_start=start,
        incident_end=end,
        failure_multiplier=ALTERNATIVE_FAILURE_MULTIPLIER,
        latency_multiplier=ALTERNATIVE_LATENCY_MULTIPLIER,
        timeout_multiplier=ALTERNATIVE_TIMEOUT_MULTIPLIER,
        target_failure_rate=ALTERNATIVE_TARGET_FAILURE_RATE,
        generation_run_id=generation_run_id,
        source_ingestion_run_id=ingestion_run_id,
        incident_seed=seed,
        ground_truth_root_cause=f"Synthetic issuer degradation injected into {sender_bank}",
        ground_truth_detail={
            "mechanism": "failure multiplier applied to one issuer across all gateways",
            "scenario": "C-alternative",
            "sender_bank": sender_bank,
            "note": "EVALUATION ONLY -- never an input to detection or RCA",
        },
        notes=(
            "Alternative-cause scenario, used to prove the RCA engine is not overfitted "
            "to the flagship gateway incident."
        ),
    )


def quiet_window(
    reference: tuple[datetime, datetime] | None = None,
) -> tuple[datetime, datetime]:
    """
    Scenario B -- an ordinary window with no injected incident.

    Deliberately a different stretch of the calendar from the flagship window, so a
    false positive here cannot be blamed on leftover simulated rows.
    """
    if reference is not None:
        return reference
    start = datetime(2024, 9, 1, 0, 0, 0, tzinfo=IST)
    return start, start + timedelta(days=3)
