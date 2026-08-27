"""
Root-cause analysis.

Produces the conclusion: what Aventum believes caused the anomaly, how confident it is,
and which specific evidence supports and contradicts that belief.

GROUND-TRUTH ISOLATION
----------------------
This module does not import `IncidentGroundTruth`, does not name the
`incident_ground_truth` table, and receives no argument carrying a known cause. It
cannot consult the answer key, because it is never handed one -- the isolation is
structural, not a matter of discipline. Scoring a diagnosis against ground truth happens
in `evaluation.py`, strictly after this module has already returned.

DECLINING TO ANSWER
-------------------
`verdict` may be INSUFFICIENT_EVIDENCE, in which case `predicted_root_cause` is None.
A diagnosis engine that always names a cause is not more useful than one that can say
"I don't know" -- it is just less honest about the cases where it was guessing.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime

from .constants import (
    RCA_CONFIDENT_THRESHOLD,
    RCA_UNCERTAIN_THRESHOLD,
    RCA_VERDICT_CONFIDENT,
    RCA_VERDICT_INSUFFICIENT,
    RCA_VERDICT_UNCERTAIN,
)
from .detect import DetectionResult
from .evidence import EvidenceBundle
from .hypothesis import Hypothesis


@dataclass
class RcaResult:
    verdict: str
    predicted_root_cause: str | None
    predicted_hypothesis_type: str | None
    predicted_gateway_id: str | None
    predicted_segment: dict | None
    confidence: float
    # ACTION-SAFETY (P1-2): absolute evidence strength travels ALONGSIDE confidence,
    # never folded into it, so a downstream consumer can require both before acting.
    # A single confidence scalar must never be sufficient to authorise a larger
    # intervention -- see docs/DAY3_P1_FIX_REPORT.md, Action-Safety Semantics.
    severity: str
    significance_sigma: float
    evidence_strength: float
    summary: str
    explanation: str
    affected_population: dict
    control_population: dict
    window_start: datetime
    window_end: datetime
    supporting_evidence_ids: list[int] = field(default_factory=list)
    contradicting_evidence_ids: list[int] = field(default_factory=list)
    alternatives_considered: list[dict] = field(default_factory=list)
    rca_fingerprint: str = ""
    elapsed_ms: float = 0.0


def _verdict_for(confidence: float, has_subject: bool) -> str:
    if not has_subject or confidence < RCA_UNCERTAIN_THRESHOLD:
        return RCA_VERDICT_INSUFFICIENT
    if confidence >= RCA_CONFIDENT_THRESHOLD:
        return RCA_VERDICT_CONFIDENT
    return RCA_VERDICT_UNCERTAIN


def _fingerprint(
    verdict: str,
    root_cause: str | None,
    confidence: float,
    hypotheses: list[Hypothesis],
    bundle: EvidenceBundle,
) -> str:
    """
    SHA-256 over the ordered analytical content.

    Deliberately excludes surrogate database IDs and wall-clock timestamps, so a clean
    rebuild that reproduces the same analysis reproduces the same fingerprint even
    though its primary keys differ.
    """
    digest = hashlib.sha256()
    digest.update(f"{verdict}|{root_cause}|{confidence:.6f}\n".encode("utf-8"))
    for hypothesis in hypotheses:
        digest.update((hypothesis.fingerprint_line() + "\n").encode("utf-8"))
    for record in sorted(bundle.records, key=lambda r: (r.cohort_key, r.evidence_type)):
        digest.update((record.fingerprint_line() + "\n").encode("utf-8"))
    return digest.hexdigest()


def run_rca(
    detection: DetectionResult,
    bundle: EvidenceBundle,
    hypotheses: list[Hypothesis],
    window_start: datetime,
    window_end: datetime,
) -> RcaResult:
    """Turn ranked hypotheses and evidence into a cited, confidence-bearing conclusion."""
    started = time.perf_counter()

    top = hypotheses[0] if hypotheses else None
    has_subject = bool(top and (top.subject_value or top.hypothesis_type == "systemic_degradation"))
    confidence = float(top.confidence) if top else 0.0
    verdict = _verdict_for(confidence, has_subject)

    alternatives = [
        {
            "hypothesis_type": h.hypothesis_type,
            "statement": h.statement,
            "subject": h.subject_value,
            "score": round(h.score, 6),
            "confidence": round(h.confidence, 4),
            "rank": h.rank,
            "supporting_evidence_count": len(h.supporting_evidence_ids),
            "contradicting_evidence_count": len(h.contradicting_evidence_ids),
            "rationale": h.rationale,
        }
        for h in hypotheses[1:]
    ]

    subject_anomaly = detection.top
    affected_population = {
        "cohort_key": subject_anomaly.cohort_key if subject_anomaly else None,
        "cohort_definition": subject_anomaly.cohort_definition if subject_anomaly else {},
        "volume": subject_anomaly.current.volume if subject_anomaly else 0,
        "baseline_metrics": subject_anomaly.baseline.as_dict() if subject_anomaly else {},
        "current_metrics": subject_anomaly.current.as_dict() if subject_anomaly else {},
        "significance_sigma": (
            round(subject_anomaly.significance_sigma, 4) if subject_anomaly else 0.0
        ),
    }

    control_record = None
    if subject_anomaly:
        for record in bundle.for_cohort(subject_anomaly.cohort_key):
            if record.evidence_type == "control_comparison":
                control_record = record
                break
    control_population = (
        control_record.control_group if control_record and control_record.control_group else {}
    )

    if verdict == RCA_VERDICT_INSUFFICIENT or top is None:
        summary = (
            "No cause could be established from the available evidence."
            if top is None
            else (
                f"Evidence is insufficient to name a root cause with acceptable confidence "
                f"(best candidate: {top.statement}, confidence {confidence:.0%})."
            )
        )
        explanation = (
            "Aventum declined to name a root cause. "
            + (top.rationale if top else "No hypothesis cleared the evidence thresholds.")
            + " Naming a cause at this confidence would misrepresent the strength of the "
            "underlying evidence."
        )
        result = RcaResult(
            verdict=verdict,
            predicted_root_cause=None,
            predicted_hypothesis_type=None,
            predicted_gateway_id=None,
            predicted_segment=None,
            confidence=round(confidence, 4),
            severity=top.subject_severity if top else "NONE",
            significance_sigma=round(top.subject_sigma, 4) if top else 0.0,
            evidence_strength=round(top.evidence_strength, 4) if top else 0.0,
            summary=summary,
            explanation=explanation,
            affected_population=affected_population,
            control_population=control_population,
            window_start=window_start,
            window_end=window_end,
            supporting_evidence_ids=list(top.supporting_evidence_ids) if top else [],
            contradicting_evidence_ids=list(top.contradicting_evidence_ids) if top else [],
            alternatives_considered=alternatives,
        )
    else:
        predicted_gateway = (
            top.subject_value if top.subject_dimension == "gateway" else None
        )
        predicted_segment = (
            {top.subject_dimension: top.subject_value}
            if top.subject_dimension and top.subject_dimension != "gateway"
            else None
        )

        summary = (
            f"{top.statement}. Confidence {confidence:.0%} "
            f"({verdict.replace('_', ' ').lower()}); evidence "
            f"{top.subject_sigma:.2f} sigma, severity {top.subject_severity}."
        )
        evidence_citation = (
            ", ".join(f"E{eid}" for eid in top.supporting_evidence_ids) or "none"
        )
        contradiction_citation = (
            ", ".join(f"E{eid}" for eid in top.contradicting_evidence_ids) or "none"
        )
        runner_up = hypotheses[1] if len(hypotheses) > 1 else None
        explanation = (
            f"{top.statement}. {top.rationale} "
            f"Supporting evidence: {evidence_citation}. "
            f"Contradicting evidence: {contradiction_citation}. "
            + (
                f"The next-best explanation considered was '{runner_up.statement}' "
                f"(score {runner_up.score:.2f} vs {top.score:.2f}); it was ranked lower "
                f"because {runner_up.rationale}"
                if runner_up
                else ""
            )
            + " All figures are computed from observed transaction amounts and modelled "
            "incident-period outcomes; no value in this explanation was generated by a "
            "language model."
        )

        result = RcaResult(
            verdict=verdict,
            predicted_root_cause=top.statement,
            predicted_hypothesis_type=top.hypothesis_type,
            predicted_gateway_id=predicted_gateway,
            predicted_segment=predicted_segment,
            confidence=round(confidence, 4),
            severity=top.subject_severity,
            significance_sigma=round(top.subject_sigma, 4),
            evidence_strength=round(top.evidence_strength, 4),
            summary=summary,
            explanation=explanation,
            affected_population=affected_population,
            control_population=control_population,
            window_start=window_start,
            window_end=window_end,
            supporting_evidence_ids=list(top.supporting_evidence_ids),
            contradicting_evidence_ids=list(top.contradicting_evidence_ids),
            alternatives_considered=alternatives,
        )

    result.rca_fingerprint = _fingerprint(
        result.verdict, result.predicted_root_cause, result.confidence, hypotheses, bundle
    )
    result.elapsed_ms = (time.perf_counter() - started) * 1000.0
    return result
