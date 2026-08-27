"""
Post-hoc evaluation: scoring a diagnosis against ground truth.

THIS MODULE IS THE ONLY PLACE IN THE PACKAGE THAT READS GROUND TRUTH.

It exists so Day 3's accuracy can be measured, and it is deliberately downstream of
everything: it takes an `RcaResult` that has ALREADY been produced and compares it to
what was injected. Nothing here feeds back into detection, evidence, hypotheses, or RCA.
Reversing that order would make the evaluation circular and the accuracy claim
meaningless.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .incident import load_ground_truth
from .rca import RcaResult


@dataclass(frozen=True)
class EvaluationResult:
    incident_id: int
    ground_truth_root_cause: str
    ground_truth_gateway_id: str | None
    predicted_root_cause: str | None
    predicted_gateway_id: str | None
    predicted_hypothesis_type: str | None
    confidence: float
    verdict: str
    gateway_correct: bool
    hypothesis_type_correct: bool

    @property
    def correct(self) -> bool:
        """A diagnosis counts as correct only if it named the right kind of cause."""
        return self.hypothesis_type_correct and self.gateway_correct


def evaluate_rca(
    session: Session,
    incident_id: int,
    rca: RcaResult,
    expected_hypothesis_type: str,
) -> EvaluationResult:
    """
    Compare a completed RCA against the incident's ground truth.

    Call this only after `run_rca` has returned. `expected_hypothesis_type` is the
    category the injected incident belongs to, supplied by the caller (a test or an
    evaluation report), not read out of the diagnosis path.
    """
    truth = load_ground_truth(session, incident_id)
    if truth is None:
        raise ValueError(f"incident {incident_id} has no recorded ground truth")

    gateway_correct = rca.predicted_gateway_id == truth.ground_truth_gateway_id
    hypothesis_correct = rca.predicted_hypothesis_type == expected_hypothesis_type

    return EvaluationResult(
        incident_id=incident_id,
        ground_truth_root_cause=truth.ground_truth_root_cause,
        ground_truth_gateway_id=truth.ground_truth_gateway_id,
        predicted_root_cause=rca.predicted_root_cause,
        predicted_gateway_id=rca.predicted_gateway_id,
        predicted_hypothesis_type=rca.predicted_hypothesis_type,
        confidence=rca.confidence,
        verdict=rca.verdict,
        gateway_correct=gateway_correct,
        hypothesis_type_correct=hypothesis_correct,
    )
