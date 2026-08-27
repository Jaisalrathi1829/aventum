"""
Day 3 orchestration.

Runs the whole chain and persists every stage, so the result is inspectable rather than
merely returned:

    inject -> simulate -> detect -> evidence -> hypotheses -> RCA

Ordering matters for one non-obvious reason: evidence is persisted BEFORE hypotheses are
built, so hypotheses cite real, queryable `evidence_id` values rather than placeholders.
An RCA explanation that cites E41 must lead a reader to row 41.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import ANALYSIS_MODEL_VERSION
from .detect import DetectionConfig, DetectionResult, detect_anomalies
from .evidence import EvidenceBundle, build_evidence
from .hypothesis import Hypothesis, build_hypotheses
from .incident import IncidentDefinition, create_incident, ensure_status
from .metrics import MetricStore
from .models import (
    Incident,
    IncidentAnalysisRun,
    IncidentAnomaly,
    IncidentEvidence,
    IncidentHypothesis,
    IncidentRcaResult,
)
from .rca import RcaResult, run_rca
from .simulate import SimulationResult, simulate_incident

_DATASET_START_SQL = text("SELECT min(timestamp) FROM transactions")


@dataclass
class PipelineResult:
    incident: Incident | None
    incident_created: bool
    simulation: SimulationResult | None
    analysis_run_id: int
    detection: DetectionResult
    evidence: EvidenceBundle
    hypotheses: list[Hypothesis]
    rca: RcaResult
    analysis_fingerprint: str
    total_ms: float

    @property
    def top_hypothesis(self) -> Hypothesis | None:
        return self.hypotheses[0] if self.hypotheses else None


def dataset_start(session: Session) -> datetime:
    value = session.execute(_DATASET_START_SQL).scalar()
    if value is None:
        raise RuntimeError("`transactions` is empty; run the Day 2A ingestion first")
    return value


def default_baseline_window(
    session: Session, incident_start: datetime
) -> tuple[datetime, datetime]:
    """
    Everything before the incident.

    Using the full pre-incident history rather than a matched short window buys a far
    more stable per-cohort baseline (months instead of days), and it cannot leak
    incident-period information backwards, which a symmetric or trailing window could.
    """
    return dataset_start(session), incident_start


def _persist_anomalies(
    session: Session,
    analysis_run_id: int,
    incident_id: int | None,
    detection: DetectionResult,
) -> dict[str, int]:
    """Persist every candidate (including suppressed ones, for audit). Returns key -> id."""
    ids: dict[str, int] = {}
    for candidate in detection.candidates:
        row = IncidentAnomaly(
            analysis_run_id=analysis_run_id,
            incident_id=incident_id,
            cohort_key=candidate.cohort_key,
            cohort_dimensions=list(candidate.dimensions),
            cohort_definition=candidate.cohort_definition,
            cohort_depth=candidate.depth,
            detection_window_start=detection.current_window[0],
            detection_window_end=detection.current_window[1],
            affected_population=candidate.current.volume,
            baseline_population=candidate.baseline.volume,
            baseline_metrics=candidate.baseline.as_dict(),
            current_metrics=candidate.current.as_dict(),
            baseline_failure_rate=round(candidate.baseline.failure_rate, 6),
            current_failure_rate=round(candidate.current.failure_rate, 6),
            absolute_delta=round(candidate.absolute_delta, 6),
            relative_delta=round(candidate.relative_delta_value, 6),
            significance_sigma=round(candidate.significance_sigma, 4),
            anomaly_score=round(candidate.score, 6),
            severity=candidate.severity,
            rank=candidate.rank,
            gmv_total=round(candidate.current.gmv_total, 2),
            gmv_at_risk=round(candidate.current.gmv_at_risk, 2),
            suppressed=candidate.suppressed,
            suppressed_by=candidate.suppressed_by,
        )
        session.add(row)
        session.flush()
        ids[candidate.cohort_key] = row.anomaly_id
    return ids


def _persist_evidence(
    session: Session,
    analysis_run_id: int,
    incident_id: int | None,
    bundle: EvidenceBundle,
    anomaly_ids: dict[str, int],
) -> None:
    """Persist evidence and stamp each record with its real database ID."""
    for record in bundle.records:
        row = IncidentEvidence(
            analysis_run_id=analysis_run_id,
            incident_id=incident_id,
            anomaly_id=anomaly_ids.get(record.cohort_key),
            evidence_type=record.evidence_type,
            metric_name=record.metric_name,
            cohort_key=record.cohort_key,
            cohort_definition=record.cohort_definition,
            gateway_id=record.gateway_id,
            segment=record.segment,
            baseline_value=(
                None if record.baseline_value is None else round(record.baseline_value, 6)
            ),
            current_value=(
                None if record.current_value is None else round(record.current_value, 6)
            ),
            delta=None if record.delta is None else round(record.delta, 6),
            relative_delta=(
                None if record.relative_delta is None else round(record.relative_delta, 6)
            ),
            significance_sigma=(
                None if record.significance_sigma is None else round(record.significance_sigma, 4)
            ),
            control_group=record.control_group,
            source_layer=record.source_layer,
            evidence_source=record.evidence_source,
            time_window_start=record.time_window_start,
            time_window_end=record.time_window_end,
            explanation=record.explanation,
        )
        session.add(row)
        session.flush()
        record.evidence_id = row.evidence_id


def _persist_hypotheses(
    session: Session,
    analysis_run_id: int,
    incident_id: int | None,
    hypotheses: list[Hypothesis],
) -> None:
    for hypothesis in hypotheses:
        session.add(
            IncidentHypothesis(
                analysis_run_id=analysis_run_id,
                incident_id=incident_id,
                hypothesis_type=hypothesis.hypothesis_type,
                hypothesis_statement=hypothesis.statement,
                subject_dimension=hypothesis.subject_dimension,
                subject_value=hypothesis.subject_value,
                score=round(hypothesis.score, 6),
                confidence=round(hypothesis.confidence, 4),
                rank=hypothesis.rank,
                supporting_evidence_ids=hypothesis.supporting_evidence_ids,
                contradicting_evidence_ids=hypothesis.contradicting_evidence_ids,
                score_components=hypothesis.components,
                rationale=hypothesis.rationale,
            )
        )
    session.flush()


def _analysis_fingerprint(
    detection: DetectionResult,
    rca: RcaResult,
    simulation: SimulationResult | None,
) -> str:
    """
    One fingerprint covering the whole analysis.

    Built from simulation content, the ranked anomaly set, and the RCA conclusion --
    never from surrogate IDs or wall-clock time, so a clean rebuild reproduces it.
    """
    digest = hashlib.sha256()
    if simulation is not None:
        digest.update(f"simulation:{simulation.simulation_fingerprint}\n".encode("utf-8"))
    for candidate in sorted(detection.candidates, key=lambda c: c.cohort_key):
        digest.update(
            (
                f"{candidate.cohort_key}|{candidate.significance_sigma:.6f}|"
                f"{candidate.score:.6f}|{candidate.severity}|{int(candidate.suppressed)}\n"
            ).encode("utf-8")
        )
    digest.update(f"rca:{rca.rca_fingerprint}\n".encode("utf-8"))
    return digest.hexdigest()


def run_analysis(
    session: Session,
    generation_run_id: int,
    current_window: tuple[datetime, datetime],
    incident: Incident | None = None,
    simulation: SimulationResult | None = None,
    baseline_window: tuple[datetime, datetime] | None = None,
    config: DetectionConfig | None = None,
) -> PipelineResult:
    """
    Run detect -> evidence -> hypotheses -> RCA over a window and persist everything.

    `incident` may be None, which is how the no-incident scenario is analysed: the
    effective-outcome surface then resolves entirely to observed history.
    """
    started = time.perf_counter()
    config = config or DetectionConfig()
    incident_id = incident.incident_id if incident is not None else None
    baseline_window = baseline_window or default_baseline_window(session, current_window[0])

    analysis_run = IncidentAnalysisRun(
        incident_id=incident_id,
        analysis_window_start=current_window[0],
        analysis_window_end=current_window[1],
        baseline_window_start=baseline_window[0],
        baseline_window_end=baseline_window[1],
        analysis_model_version=ANALYSIS_MODEL_VERSION,
        detection_config=config.as_dict(),
        status="RUNNING",
    )
    session.add(analysis_run)
    session.flush()

    store = MetricStore(session, generation_run_id, incident_id)

    detection = detect_anomalies(
        session,
        generation_run_id=generation_run_id,
        baseline_window=baseline_window,
        current_window=current_window,
        incident_id=incident_id,
        config=config,
        store=store,
    )
    anomaly_ids = _persist_anomalies(session, analysis_run.analysis_run_id, incident_id, detection)
    if incident is not None and detection.reported:
        ensure_status(session, incident, "DETECTED")

    bundle = build_evidence(detection)
    _persist_evidence(session, analysis_run.analysis_run_id, incident_id, bundle, anomaly_ids)

    hypotheses = build_hypotheses(detection, bundle)
    _persist_hypotheses(session, analysis_run.analysis_run_id, incident_id, hypotheses)

    rca = run_rca(
        detection,
        bundle,
        hypotheses,
        window_start=current_window[0],
        window_end=current_window[1],
    )
    session.add(
        IncidentRcaResult(
            analysis_run_id=analysis_run.analysis_run_id,
            incident_id=incident_id,
            verdict=rca.verdict,
            predicted_root_cause=rca.predicted_root_cause,
            predicted_hypothesis_type=rca.predicted_hypothesis_type,
            predicted_gateway_id=rca.predicted_gateway_id,
            predicted_segment=rca.predicted_segment,
            confidence=rca.confidence,
            summary=rca.summary,
            explanation=rca.explanation,
            affected_population=rca.affected_population,
            control_population=rca.control_population,
            incident_window_start=rca.window_start,
            incident_window_end=rca.window_end,
            supporting_evidence_ids=rca.supporting_evidence_ids,
            contradicting_evidence_ids=rca.contradicting_evidence_ids,
            alternatives_considered=rca.alternatives_considered,
            rca_fingerprint=rca.rca_fingerprint,
        )
    )
    if incident is not None and rca.verdict != "INSUFFICIENT_EVIDENCE":
        ensure_status(session, incident, "DIAGNOSED")

    fingerprint = _analysis_fingerprint(detection, rca, simulation)
    total_ms = (time.perf_counter() - started) * 1000.0

    analysis_run.status = "SUCCEEDED"
    analysis_run.cohorts_scanned = detection.cohorts_scanned
    analysis_run.anomalies_found = len(detection.reported)
    analysis_run.analysis_fingerprint = fingerprint
    analysis_run.detection_ms = round(detection.elapsed_ms, 3)
    analysis_run.evidence_ms = round(bundle.elapsed_ms, 3)
    analysis_run.rca_ms = round(rca.elapsed_ms, 3)
    analysis_run.finished_at = datetime.now(timezone.utc)
    session.flush()

    return PipelineResult(
        incident=incident,
        incident_created=False,
        simulation=simulation,
        analysis_run_id=analysis_run.analysis_run_id,
        detection=detection,
        evidence=bundle,
        hypotheses=hypotheses,
        rca=rca,
        analysis_fingerprint=fingerprint,
        total_ms=total_ms,
    )


def run_incident_pipeline(
    session: Session,
    definition: IncidentDefinition,
    config: DetectionConfig | None = None,
    baseline_window: tuple[datetime, datetime] | None = None,
) -> PipelineResult:
    """Inject an incident, simulate it, then analyse it end to end."""
    incident, created = create_incident(session, definition)
    simulation = simulate_incident(session, incident)
    ensure_status(session, incident, "ACTIVE")

    result = run_analysis(
        session,
        generation_run_id=incident.generation_run_id,
        current_window=(incident.incident_start, incident.incident_end),
        incident=incident,
        simulation=simulation,
        baseline_window=baseline_window,
        config=config,
    )
    result.incident_created = created
    return result


def run_quiet_analysis(
    session: Session,
    generation_run_id: int,
    window: tuple[datetime, datetime],
    config: DetectionConfig | None = None,
) -> PipelineResult:
    """Analyse a window with no injected incident -- the false-positive control."""
    return run_analysis(
        session,
        generation_run_id=generation_run_id,
        current_window=window,
        incident=None,
        simulation=None,
        config=config,
    )
