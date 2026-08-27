"""
The Day 4 interface.

Day 4 (counterfactual simulation, agent orchestration, bounded recommendation) consumes
Day 3 through these functions and nothing else. It must not reconstruct a diagnosis by
querying `incident_anomalies`/`incident_evidence` itself: that would couple Day 4 to
Day 3's schema and, worse, let it assemble a different version of the same conclusion.

Note what is absent by design: no function here returns ground truth. Day 4's agent
reasons over evidence and conclusions, and the answer key is not part of that surface.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Incident,
    IncidentAnalysisRun,
    IncidentAnomaly,
    IncidentEvidence,
    IncidentHypothesis,
    IncidentRcaResult,
    IncidentSimulationRun,
)


@dataclass
class IncidentView:
    incident_id: int
    incident_name: str
    incident_type: str
    affected_gateway: str | None
    affected_segment: dict | None
    start: str
    end: str
    severity: float | None
    status: str
    provenance: str


@dataclass
class SimulatedOutcomeSummary:
    incident_id: int
    simulation_run_id: int
    rows_in_window: int
    rows_simulated: int
    rows_changed: int
    simulation_fingerprint: str
    provenance: str


@dataclass
class DetectionView:
    anomaly_id: int
    severity: str
    anomaly_score: float
    significance_sigma: float
    cohort_key: str
    affected_population: int
    baseline_metrics: dict
    current_metrics: dict
    detection_window: dict
    gmv_at_risk: float
    rank: int


@dataclass
class EvidenceView:
    evidence_id: int
    evidence_type: str
    metric: str
    baseline: float | None
    current: float | None
    delta: float | None
    significance_sigma: float | None
    cohort: dict
    control: dict | None
    source_layer: str
    evidence_source: str
    explanation: str


@dataclass
class RcaView:
    incident_id: int | None
    analysis_run_id: int
    verdict: str
    predicted_root_cause: str | None
    predicted_hypothesis_type: str | None
    predicted_gateway_id: str | None
    predicted_segment: dict | None
    confidence: float
    summary: str
    explanation: str
    supporting_evidence_ids: list[int]
    contradicting_evidence_ids: list[int]
    alternatives_considered: list[dict]
    affected_population: dict
    control_population: dict
    rca_fingerprint: str


@dataclass
class Day4Handoff:
    """Everything Day 4 needs, in one object, with no raw-table access required."""

    incident: IncidentView | None
    simulation: SimulatedOutcomeSummary | None
    detections: list[DetectionView] = field(default_factory=list)
    evidence: list[EvidenceView] = field(default_factory=list)
    rca: RcaView | None = None

    def as_dict(self) -> dict:
        return {
            "incident": asdict(self.incident) if self.incident else None,
            "simulation": asdict(self.simulation) if self.simulation else None,
            "detections": [asdict(d) for d in self.detections],
            "evidence": [asdict(e) for e in self.evidence],
            "rca": asdict(self.rca) if self.rca else None,
        }


def _iso(value) -> str:
    return value.isoformat() if value is not None else ""


def build_handoff(session: Session, analysis_run_id: int) -> Day4Handoff:
    """Assemble the complete Day 4 handoff for one analysis run."""
    analysis_run = session.get(IncidentAnalysisRun, analysis_run_id)
    if analysis_run is None:
        raise ValueError(f"no analysis run {analysis_run_id}")

    incident_view = None
    simulation_view = None
    if analysis_run.incident_id is not None:
        incident = session.get(Incident, analysis_run.incident_id)
        if incident is not None:
            incident_view = IncidentView(
                incident_id=incident.incident_id,
                incident_name=incident.incident_name,
                incident_type=incident.incident_type,
                affected_gateway=incident.affected_gateway_id,
                affected_segment=incident.affected_segment,
                start=_iso(incident.incident_start),
                end=_iso(incident.incident_end),
                severity=(
                    float(incident.target_failure_rate)
                    if incident.target_failure_rate is not None
                    else None
                ),
                status=incident.status,
                provenance="SYNTHETIC_INCIDENT",
            )

        run = session.scalar(
            select(IncidentSimulationRun)
            .where(IncidentSimulationRun.incident_id == analysis_run.incident_id)
            .order_by(IncidentSimulationRun.simulation_run_id.desc())
            .limit(1)
        )
        if run is not None:
            simulation_view = SimulatedOutcomeSummary(
                incident_id=run.incident_id,
                simulation_run_id=run.simulation_run_id,
                rows_in_window=run.rows_in_window,
                rows_simulated=run.rows_simulated,
                rows_changed=run.rows_changed,
                simulation_fingerprint=run.simulation_fingerprint or "",
                provenance="SIMULATED_INCIDENT_OUTCOME",
            )

    anomalies = session.scalars(
        select(IncidentAnomaly)
        .where(
            IncidentAnomaly.analysis_run_id == analysis_run_id,
            IncidentAnomaly.suppressed.is_(False),
        )
        .order_by(IncidentAnomaly.rank)
    ).all()

    detections = [
        DetectionView(
            anomaly_id=row.anomaly_id,
            severity=row.severity,
            anomaly_score=float(row.anomaly_score),
            significance_sigma=float(row.significance_sigma),
            cohort_key=row.cohort_key,
            affected_population=row.affected_population,
            baseline_metrics=row.baseline_metrics,
            current_metrics=row.current_metrics,
            detection_window={
                "start": _iso(row.detection_window_start),
                "end": _iso(row.detection_window_end),
            },
            gmv_at_risk=float(row.gmv_at_risk),
            rank=row.rank,
        )
        for row in anomalies
    ]

    evidence_rows = session.scalars(
        select(IncidentEvidence)
        .where(IncidentEvidence.analysis_run_id == analysis_run_id)
        .order_by(IncidentEvidence.evidence_id)
    ).all()

    evidence = [
        EvidenceView(
            evidence_id=row.evidence_id,
            evidence_type=row.evidence_type,
            metric=row.metric_name,
            baseline=None if row.baseline_value is None else float(row.baseline_value),
            current=None if row.current_value is None else float(row.current_value),
            delta=None if row.delta is None else float(row.delta),
            significance_sigma=(
                None if row.significance_sigma is None else float(row.significance_sigma)
            ),
            cohort=row.cohort_definition,
            control=row.control_group,
            source_layer=row.source_layer,
            evidence_source=row.evidence_source,
            explanation=row.explanation,
        )
        for row in evidence_rows
    ]

    rca_row = session.scalar(
        select(IncidentRcaResult).where(IncidentRcaResult.analysis_run_id == analysis_run_id)
    )
    rca_view = None
    if rca_row is not None:
        rca_view = RcaView(
            incident_id=rca_row.incident_id,
            analysis_run_id=rca_row.analysis_run_id,
            verdict=rca_row.verdict,
            predicted_root_cause=rca_row.predicted_root_cause,
            predicted_hypothesis_type=rca_row.predicted_hypothesis_type,
            predicted_gateway_id=rca_row.predicted_gateway_id,
            predicted_segment=rca_row.predicted_segment,
            confidence=float(rca_row.confidence),
            summary=rca_row.summary,
            explanation=rca_row.explanation,
            supporting_evidence_ids=list(rca_row.supporting_evidence_ids or []),
            contradicting_evidence_ids=list(rca_row.contradicting_evidence_ids or []),
            alternatives_considered=list(rca_row.alternatives_considered or []),
            affected_population=rca_row.affected_population,
            control_population=rca_row.control_population,
            rca_fingerprint=rca_row.rca_fingerprint,
        )

    return Day4Handoff(
        incident=incident_view,
        simulation=simulation_view,
        detections=detections,
        evidence=evidence,
        rca=rca_view,
    )


def ranked_hypotheses(session: Session, analysis_run_id: int) -> list[dict]:
    """The full competing-hypothesis set, ranked -- Day 4 may show alternatives."""
    rows = session.scalars(
        select(IncidentHypothesis)
        .where(IncidentHypothesis.analysis_run_id == analysis_run_id)
        .order_by(IncidentHypothesis.rank)
    ).all()
    return [
        {
            "hypothesis_id": row.hypothesis_id,
            "hypothesis_type": row.hypothesis_type,
            "statement": row.hypothesis_statement,
            "subject_dimension": row.subject_dimension,
            "subject_value": row.subject_value,
            "score": float(row.score),
            "confidence": float(row.confidence),
            "rank": row.rank,
            "supporting_evidence_ids": list(row.supporting_evidence_ids or []),
            "contradicting_evidence_ids": list(row.contradicting_evidence_ids or []),
            "score_components": row.score_components,
            "rationale": row.rationale,
        }
        for row in rows
    ]
