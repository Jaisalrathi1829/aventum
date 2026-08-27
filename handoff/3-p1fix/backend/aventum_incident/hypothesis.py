"""
The hypothesis engine.

Generates a fixed set of competing explanations and scores each against the collected
evidence. Every category is always evaluated, even when nothing supports it: a system
that only scores the explanation it already likes cannot be said to have considered
alternatives, and its confidence number would mean nothing.

WHAT ACTUALLY DISCRIMINATES
---------------------------
Two signals do the real work, and neither mentions any specific gateway or bank:

  1. WHICH DIMENSION carries the localised, control-divergent anomaly. A gateway
     problem shows up as one gateway diverging from its peers; an issuer problem shows
     up as one bank diverging from its peers, spread across all gateways. The engine
     simply asks each hypothesis's own dimension what it sees.

  2. THE RESPONSE-MIX TILT. Infrastructure failures skew the failure mix toward
     PROCESSING_ERROR/TIMEOUT; an issuer declining transactions does not -- its failures
     stay in the issuer-side families. Measured as the change in
     `infrastructure_side / failures`, this supports infrastructure-flavoured
     hypotheses and counts against issuer-flavoured ones.

Scores are a documented weighted sum of measured components, stored alongside the
result, so a reviewer can see exactly why one hypothesis outranked another.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .constants import (
    DIMENSION_GATEWAY,
    EVIDENCE_STRENGTH_SATURATION,
    EVIDENCE_BLAST_RADIUS,
    EVIDENCE_CONFOUNDING,
    EVIDENCE_CONTROL_COMPARISON,
    EVIDENCE_RESPONSE_MIX,
    EVIDENCE_TEMPORAL,
    HYPOTHESIS_SUBJECT_DIMENSION,
    INDEPENDENCE_COLLAPSE_THRESHOLD,
    MIN_ABSOLUTE_RATE_DELTA,
)
from .detect import AnomalyCandidate, DetectionResult
from .statistics import anomaly_score, severity_for
from .evidence import EvidenceBundle, EvidenceRecord

# Component weights. They sum to 1.0; the response-mix term is applied afterwards as a
# bounded modifier so it can adjust a ranking but never manufacture one on its own.
#
# `independence` carries the largest single weight deliberately. A degraded gateway drags
# every region, network and bank that routes through it into looking mildly anomalous,
# and those shadows are otherwise indistinguishable from real causes on signal strength
# alone. Asking whether a cohort still moves once the leading suspect is removed is the
# component that actually separates them.
WEIGHT_SIGNAL = 0.30
WEIGHT_DIVERGENCE = 0.20
WEIGHT_LOCALISATION = 0.15
WEIGHT_TEMPORAL = 0.05
WEIGHT_INDEPENDENCE = 0.30
RESPONSE_TILT_WEIGHT = 0.15

# Sigma at which the statistical signal component saturates.
SIGNAL_SATURATION_SIGMA = 12.0

# Hypotheses whose mechanism lives in the payment infrastructure. A shift toward
# infrastructure-side response codes supports these and counts against the others.
INFRASTRUCTURE_FLAVOURED = ("gateway_degradation", "network_segment_degradation")


@dataclass
class Hypothesis:
    hypothesis_type: str
    statement: str
    subject_dimension: str | None
    subject_value: str | None
    score: float
    confidence: float
    rank: int
    # Absolute evidence strength of this hypothesis's subject, in [0, 1]. Kept separate
    # from `score` (which measures attribution quality) so confidence can require both.
    evidence_strength: float = 0.0
    subject_severity: str = "NONE"
    subject_sigma: float = 0.0
    supporting_evidence_ids: list[int] = field(default_factory=list)
    contradicting_evidence_ids: list[int] = field(default_factory=list)
    components: dict = field(default_factory=dict)
    rationale: str = ""

    def fingerprint_line(self) -> str:
        return (
            f"{self.hypothesis_type}|{self.subject_dimension}|{self.subject_value}|"
            f"{self.score:.6f}"
        )


def _best_anomaly_on(
    detection: DetectionResult, dimension: str
) -> AnomalyCandidate | None:
    """Strongest reported single-dimension anomaly on `dimension`, if any."""
    matches = [
        candidate
        for candidate in detection.reported
        if candidate.dimensions == (dimension,)
    ]
    if not matches:
        return None
    return max(matches, key=lambda c: c.score)


def _evidence_of(
    bundle: EvidenceBundle, cohort_key: str, evidence_type: str
) -> EvidenceRecord | None:
    for record in bundle.for_cohort(cohort_key):
        if record.evidence_type == evidence_type:
            return record
    return None


def evidence_strength_from(anomaly_score_value: float) -> float:
    """
    Absolute evidence strength in [0, 1], from a cohort's anomaly score.

    `anomaly_score` is already significance x effect size, so this is a single
    saturating measure of "how much real evidence is there", entirely separate from how
    cleanly that evidence points at one explanation.
    """
    if anomaly_score_value <= 0:
        return 0.0
    return min(1.0, anomaly_score_value / EVIDENCE_STRENGTH_SATURATION)


def confidence_from(evidence_strength: float, attribution: float) -> float:
    """
    Combine absolute evidence strength with attribution quality.

    CONFIDENCE MODEL (P1-2)
    -----------------------
    Confidence is the GEOMETRIC MEAN of two independent questions:

        evidence_strength -- how much real evidence is there?   (significance x effect)
        attribution       -- how cleanly does it point at ONE explanation?
                             (the weighted component score, scaled by how decisively the
                             winner beats its runner-up)

        confidence = sqrt(evidence_strength x attribution)

    A geometric mean is used rather than a weighted sum because the two factors must not
    substitute for one another. Under a sum, a perfectly-attributed but weak signal can
    buy its way to high confidence -- which is exactly the defect this replaces, where a
    5.16 sigma incident with no competing hypotheses outscored a 9.26 sigma one that had
    real rivals. Under a product, a near-zero factor drags the result down and cannot be
    compensated.

    Two properties follow directly and are asserted in the tests:
      - confidence <= sqrt(evidence_strength), so weak evidence has a hard ceiling and
        cannot reach CONFIDENT however decisive the attribution.
      - confidence <= sqrt(attribution), so a strong but ambiguous signal is likewise
        capped -- strength alone does not authorise certainty either.

    Bounded [0, 1], deterministic, and a pure function of two already-computed values.
    """
    strength = max(0.0, min(1.0, evidence_strength))
    quality = max(0.0, min(1.0, attribution))
    return (strength * quality) ** 0.5


def _infra_share(failures: int, infrastructure_side: int) -> float:
    """Share of this cohort's failures that are infrastructure-side."""
    return (infrastructure_side / failures) if failures else 0.0


def _score_localised(
    hypothesis_type: str,
    subject: AnomalyCandidate,
    bundle: EvidenceBundle,
) -> tuple[float, dict, list[int], list[int], str]:
    """Score one hypothesis whose subject is a specific cohort value."""
    supporting: list[int] = []
    contradicting: list[int] = []

    # --- statistical signal ---------------------------------------------------
    signal = min(1.0, max(0.0, subject.significance_sigma / SIGNAL_SATURATION_SIGMA))
    failure_evidence = _evidence_of(bundle, subject.cohort_key, "failure_rate")
    if failure_evidence and failure_evidence.evidence_id is not None:
        supporting.append(failure_evidence.evidence_id)

    # --- divergence from the control group -------------------------------------
    control_evidence = _evidence_of(bundle, subject.cohort_key, EVIDENCE_CONTROL_COMPARISON)
    control_delta = float(control_evidence.delta or 0.0) if control_evidence else 0.0
    if subject.absolute_delta > 0:
        divergence = min(
            1.0, max(0.0, (subject.absolute_delta - control_delta) / subject.absolute_delta)
        )
    else:
        divergence = 0.0
    if control_evidence and control_evidence.evidence_id is not None:
        # Controls holding steady supports a localised cause; controls moving with the
        # subject argues the cause is broader than this cohort.
        if control_delta < MIN_ABSOLUTE_RATE_DELTA:
            supporting.append(control_evidence.evidence_id)
        else:
            contradicting.append(control_evidence.evidence_id)

    # --- localisation (inverse blast radius) -----------------------------------
    blast_evidence = _evidence_of(bundle, subject.cohort_key, EVIDENCE_BLAST_RADIUS)
    blast_radius = float(blast_evidence.current_value or 0.0) if blast_evidence else 1.0
    localisation = max(0.0, 1.0 - blast_radius)
    if blast_evidence and blast_evidence.evidence_id is not None:
        if blast_radius <= 0.5:
            supporting.append(blast_evidence.evidence_id)
        else:
            contradicting.append(blast_evidence.evidence_id)

    # --- temporal confinement ---------------------------------------------------
    temporal_evidence = _evidence_of(bundle, subject.cohort_key, EVIDENCE_TEMPORAL)
    if temporal_evidence is not None:
        temporal_sigma = float(temporal_evidence.significance_sigma or 0.0)
        temporal = min(1.0, max(0.0, temporal_sigma / SIGNAL_SATURATION_SIGMA))
        if temporal_evidence.evidence_id is not None:
            if temporal_sigma >= 3.0:
                supporting.append(temporal_evidence.evidence_id)
            else:
                contradicting.append(temporal_evidence.evidence_id)
    else:
        temporal = 0.0

    # --- independence from the leading confounder -------------------------------
    confounding_evidence = _evidence_of(bundle, subject.cohort_key, EVIDENCE_CONFOUNDING)
    if confounding_evidence is not None:
        # `relative_delta` carries the retained share of the original movement.
        independence = max(0.0, min(1.0, float(confounding_evidence.relative_delta or 0.0)))
        if confounding_evidence.evidence_id is not None:
            if independence >= INDEPENDENCE_COLLAPSE_THRESHOLD:
                supporting.append(confounding_evidence.evidence_id)
            else:
                contradicting.append(confounding_evidence.evidence_id)
    else:
        # Nothing else was anomalous enough to explain this cohort away.
        independence = 1.0

    base = (
        WEIGHT_SIGNAL * signal
        + WEIGHT_DIVERGENCE * divergence
        + WEIGHT_LOCALISATION * localisation
        + WEIGHT_TEMPORAL * temporal
        + WEIGHT_INDEPENDENCE * independence
    )

    # --- response-mix tilt (bounded modifier) -----------------------------------
    mix_evidence = _evidence_of(bundle, subject.cohort_key, EVIDENCE_RESPONSE_MIX)
    baseline_share = _infra_share(subject.baseline.failures, subject.baseline.infrastructure_side)
    current_share = _infra_share(subject.current.failures, subject.current.infrastructure_side)
    tilt = current_share - baseline_share

    infrastructure_flavoured = hypothesis_type in INFRASTRUCTURE_FLAVOURED
    alignment = tilt if infrastructure_flavoured else -tilt
    modifier = RESPONSE_TILT_WEIGHT * max(-1.0, min(1.0, alignment / 0.10))

    if mix_evidence and mix_evidence.evidence_id is not None:
        if alignment > 0.01:
            supporting.append(mix_evidence.evidence_id)
        elif alignment < -0.01:
            contradicting.append(mix_evidence.evidence_id)

    score = max(0.0, min(1.0, base + modifier))

    components = {
        "signal": round(signal, 6),
        "divergence": round(divergence, 6),
        "localisation": round(localisation, 6),
        "temporal": round(temporal, 6),
        "independence": round(independence, 6),
        "base_score": round(base, 6),
        "response_tilt": round(tilt, 6),
        "response_tilt_modifier": round(modifier, 6),
        "significance_sigma": round(subject.significance_sigma, 4),
        "absolute_delta": round(subject.absolute_delta, 6),
        "control_delta": round(control_delta, 6),
        "blast_radius": round(blast_radius, 6),
    }

    rationale = (
        f"{subject.cohort_key} rose {subject.absolute_delta:.2%} "
        f"({subject.significance_sigma:.1f} sigma) while its control group moved "
        f"{control_delta:+.2%}; it retains {independence:.0%} of that movement once the "
        f"leading alternative suspect is excluded; blast radius {blast_radius:.0%}; "
        f"infrastructure-side share of failures moved {tilt:+.1%}, which "
        f"{'supports' if alignment > 0 else 'counts against'} an "
        f"{'infrastructure' if infrastructure_flavoured else 'issuer/method'}-side cause."
    )
    return score, components, supporting, contradicting, rationale


def _score_systemic(
    detection: DetectionResult, bundle: EvidenceBundle
) -> tuple[float, dict, list[int], list[int], str]:
    """
    Score the "everything degraded" explanation from whole-population metrics.

    Scored independently of any single cohort so it can genuinely win: if the entire
    population moved together, no localised hypothesis should be allowed to claim credit
    for it just because one cohort happened to move most.
    """
    baseline = detection.population_baseline
    current = detection.population_current
    delta = current.failure_rate - baseline.failure_rate

    from .statistics import two_proportion_z

    sigma = two_proportion_z(
        baseline.failures, baseline.volume, current.failures, current.volume
    )
    signal = min(1.0, max(0.0, sigma / SIGNAL_SATURATION_SIGMA))

    # Breadth: the fraction of gateways that moved materially. A genuine systemic event
    # moves most of them; a single-gateway incident moves one.
    gateway_current = detection.store.metrics((DIMENSION_GATEWAY,), detection.current_window)
    gateway_baseline = detection.store.indexed(
        (DIMENSION_GATEWAY,), detection.baseline_window
    )
    moved = 0
    comparable = 0
    for cohort in gateway_current:
        prior = gateway_baseline.get(cohort.cohort_key)
        if prior is None or prior.volume == 0:
            continue
        comparable += 1
        if cohort.failure_rate - prior.failure_rate >= MIN_ABSOLUTE_RATE_DELTA:
            moved += 1
    breadth = (moved / comparable) if comparable else 0.0

    # A genuinely systemic event is not explained away by removing any single cohort.
    # A large issuer or gateway degrading looks systemic on breadth alone -- its traffic
    # touches every other dimension -- so the honest test is whether the population is
    # still moving once the leading suspect is taken out. If it snaps back to baseline,
    # the cause was that cohort, not the system.
    leading = detection.top
    independence = 1.0
    if leading is not None and len(leading.dimensions) == 1:
        exclusion = dict(leading.cohort_definition)
        residual_current = detection.store.residual((), detection.current_window, exclusion).get(
            "ALL"
        )
        residual_baseline = detection.store.residual(
            (), detection.baseline_window, exclusion
        ).get("ALL")
        if residual_current is not None and residual_baseline is not None and delta > 0:
            residual_delta = residual_current.failure_rate - residual_baseline.failure_rate
            independence = max(0.0, min(1.0, residual_delta / delta))

    score = max(0.0, min(1.0, (0.5 * signal + 0.5 * breadth) * independence))
    if delta < MIN_ABSOLUTE_RATE_DELTA:
        # The population barely moved; a systemic explanation is not on the table.
        score = min(score, 0.15)

    supporting: list[int] = []
    contradicting: list[int] = []
    # Every blast-radius record showing a narrow footprint argues against systemic.
    for record in bundle.records:
        if record.evidence_type != EVIDENCE_BLAST_RADIUS or record.evidence_id is None:
            continue
        if float(record.current_value or 0.0) >= 0.6:
            supporting.append(record.evidence_id)
        else:
            contradicting.append(record.evidence_id)

    components = {
        "signal": round(signal, 6),
        "breadth": round(breadth, 6),
        "independence": round(independence, 6),
        "population_delta": round(delta, 6),
        "population_sigma": round(sigma, 4),
        "gateways_moved": moved,
        "gateways_comparable": comparable,
        "leading_cohort_excluded": leading.cohort_key if leading is not None else None,
    }
    rationale = (
        f"Population failure rate moved {delta:+.2%} ({sigma:.1f} sigma) and {moved} of "
        f"{comparable} gateways moved materially, so the footprint is "
        f"{'broad' if breadth >= 0.6 else 'narrow'}. "
        + (
            f"Excluding {leading.cohort_key}, the population retains "
            f"{independence:.0%} of that movement"
            + (
                ", so the shift is not attributable to one cohort."
                if independence >= INDEPENDENCE_COLLAPSE_THRESHOLD
                else ", so the apparent fleet-wide shift is largely that one cohort."
            )
            if leading is not None
            else ""
        )
    )
    return score, components, supporting, contradicting, rationale


def build_hypotheses(
    detection: DetectionResult,
    bundle: EvidenceBundle,
) -> list[Hypothesis]:
    """
    Build and rank the full competing-hypothesis set.

    Always returns one hypothesis per category, ranked. Categories with no supporting
    anomaly score near zero and say so, rather than being silently omitted -- Day 4 and
    any human reader are entitled to see what was ruled out.
    """
    hypotheses: list[Hypothesis] = []

    statements = {
        "gateway_degradation": "Payment gateway {subject} is degraded",
        "issuer_degradation": "Issuer/bank {subject} is declining or failing transactions",
        "payment_method_degradation": "Payment method {subject} is degraded",
        "network_segment_degradation": "Network segment {subject} is degraded",
        "systemic_degradation": "A systemic, fleet-wide degradation is in progress",
    }

    for hypothesis_type, dimension in HYPOTHESIS_SUBJECT_DIMENSION.items():
        if dimension is None:
            score, components, supporting, contradicting, rationale = _score_systemic(
                detection, bundle
            )
            hypotheses.append(
                Hypothesis(
                    hypothesis_type=hypothesis_type,
                    statement=statements[hypothesis_type],
                    subject_dimension=None,
                    subject_value=None,
                    score=score,
                    confidence=0.0,
                    rank=0,
                    # Population-level strength: the whole-fleet move is this
                    # hypothesis's evidence, so its own sigma and effect define it.
                    evidence_strength=evidence_strength_from(
                        anomaly_score(
                            float(components.get("population_sigma", 0.0)),
                            float(components.get("population_delta", 0.0)),
                        )
                    ),
                    subject_severity=severity_for(
                        float(components.get("population_sigma", 0.0))
                    ),
                    subject_sigma=float(components.get("population_sigma", 0.0)),
                    supporting_evidence_ids=supporting,
                    contradicting_evidence_ids=contradicting,
                    components=components,
                    rationale=rationale,
                )
            )
            continue

        subject = _best_anomaly_on(detection, dimension)
        if subject is None:
            hypotheses.append(
                Hypothesis(
                    hypothesis_type=hypothesis_type,
                    statement=statements[hypothesis_type].format(subject="(no candidate)"),
                    subject_dimension=dimension,
                    subject_value=None,
                    score=0.0,
                    confidence=0.0,
                    rank=0,
                    evidence_strength=0.0,
                    supporting_evidence_ids=[],
                    contradicting_evidence_ids=[],
                    components={"reason": "no significant anomaly on this dimension"},
                    rationale=(
                        f"No cohort on the {dimension} dimension cleared the detection "
                        f"thresholds, so there is no evidence for this explanation."
                    ),
                )
            )
            continue

        score, components, supporting, contradicting, rationale = _score_localised(
            hypothesis_type, subject, bundle
        )
        subject_value = subject.cohort_definition.get(dimension)
        hypotheses.append(
            Hypothesis(
                hypothesis_type=hypothesis_type,
                statement=statements[hypothesis_type].format(subject=subject_value),
                subject_dimension=dimension,
                subject_value=subject_value,
                score=score,
                confidence=0.0,
                rank=0,
                # The subject cohort's own anomaly score IS the absolute evidence for
                # this hypothesis.
                evidence_strength=evidence_strength_from(subject.score),
                subject_severity=subject.severity,
                subject_sigma=subject.significance_sigma,
                supporting_evidence_ids=supporting,
                contradicting_evidence_ids=contradicting,
                components=components,
                rationale=rationale,
            )
        )

    # Deterministic ordering: score desc, then type name so ties never depend on dict order.
    hypotheses.sort(key=lambda h: (-h.score, h.hypothesis_type))

    top_score = hypotheses[0].score if hypotheses else 0.0
    second_score = hypotheses[1].score if len(hypotheses) > 1 else 0.0

    for position, hypothesis in enumerate(hypotheses, start=1):
        hypothesis.rank = position

        # ATTRIBUTION QUALITY -- how cleanly this hypothesis wins. Decisiveness matters:
        # a hypothesis that barely beats its runner-up is a weaker attribution than one
        # that wins outright. This is the whole of the pre-fix confidence value.
        if position == 1 and top_score > 0:
            margin = (top_score - second_score) / top_score
            attribution = max(0.0, min(1.0, top_score * (0.5 + 0.5 * margin)))
        else:
            margin = 0.0
            attribution = max(0.0, min(1.0, hypothesis.score * 0.5))

        # CONFIDENCE -- attribution quality tempered by absolute evidence strength, so
        # a cleanly-attributed weak signal can no longer outrank a strong one (P1-2).
        hypothesis.confidence = round(
            confidence_from(hypothesis.evidence_strength, attribution), 4
        )
        hypothesis.components["attribution_quality"] = round(attribution, 6)
        hypothesis.components["attribution_margin"] = round(margin, 6)
        hypothesis.components["evidence_strength"] = round(hypothesis.evidence_strength, 6)

    return hypotheses
