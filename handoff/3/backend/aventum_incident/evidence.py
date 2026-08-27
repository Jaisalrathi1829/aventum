"""
The evidence engine.

Turns detected anomalies into quantified, traceable evidence records. Every value is
computed by a named analytical step (recorded in `evidence_source`) from cohort
aggregates; nothing here is estimated, rounded to taste, or narrated. An RCA statement
that cannot point at an evidence_id produced by this module is not a finding Aventum
will make.

Each record also carries `source_layer` -- OBSERVED, SYNTHETIC, or SIMULATED -- because
a number computed over a modelled incident window and a number computed over observed
history are different kinds of claim, and flattening them is exactly what the truth
model forbids.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from .constants import (
    EVIDENCE_BLAST_RADIUS,
    EVIDENCE_CONFOUNDING,
    EVIDENCE_CONTROL_COMPARISON,
    EVIDENCE_FAILURE_RATE,
    EVIDENCE_GMV,
    EVIDENCE_LATENCY,
    EVIDENCE_RESPONSE_MIX,
    EVIDENCE_TEMPORAL,
    INDEPENDENCE_COLLAPSE_THRESHOLD,
    MIN_ABSOLUTE_RATE_DELTA,
    SINGLE_DIMENSIONS,
    SOURCE_LAYER_OBSERVED,
)
from .detect import AnomalyCandidate, DetectionResult
from .metrics import CohortMetrics
from .statistics import relative_delta, two_proportion_z

# How many reported anomalies get a full evidence workup. The ranking is already
# score-ordered, and building evidence for every marginal cohort would bury the
# hypothesis engine in near-duplicate records.
EVIDENCE_SUBJECT_LIMIT = 5


@dataclass
class EvidenceRecord:
    """One evidence item, pre-persistence. Field names mirror `incident_evidence`."""

    evidence_type: str
    metric_name: str
    cohort_key: str
    cohort_definition: dict
    gateway_id: str | None
    segment: dict | None
    baseline_value: float | None
    current_value: float | None
    delta: float | None
    relative_delta: float | None
    significance_sigma: float | None
    control_group: dict | None
    source_layer: str
    evidence_source: str
    time_window_start: datetime
    time_window_end: datetime
    explanation: str
    # Filled in after the row is persisted, so hypotheses can cite real IDs.
    evidence_id: int | None = None

    def fingerprint_line(self) -> str:
        """Stable rendering for the reproducibility fingerprint. Excludes surrogate IDs."""
        return (
            f"{self.evidence_type}|{self.metric_name}|{self.cohort_key}|"
            f"{_num(self.baseline_value)}|{_num(self.current_value)}|"
            f"{_num(self.delta)}|{_num(self.significance_sigma)}|{self.source_layer}"
        )


def _num(value: float | None) -> str:
    return "NULL" if value is None else f"{value:.6f}"


@dataclass(frozen=True)
class EvidenceBundle:
    records: list[EvidenceRecord]
    subjects: list[AnomalyCandidate]
    elapsed_ms: float

    def for_cohort(self, cohort_key: str) -> list[EvidenceRecord]:
        return [record for record in self.records if record.cohort_key == cohort_key]


def _aggregate(metrics: list[CohortMetrics]) -> dict:
    """
    Sum a set of sibling cohorts into one control aggregate.

    Counts and GMV sum exactly. Latency does not -- a pooled percentile cannot be
    recovered from per-cohort percentiles -- so the latency figure is explicitly named a
    volume-weighted mean rather than presented as a true p95.
    """
    volume = sum(m.volume for m in metrics)
    failures = sum(m.failures for m in metrics)
    infrastructure = sum(m.infrastructure_side for m in metrics)
    gmv_total = sum(m.gmv_total for m in metrics)
    gmv_at_risk = sum(m.gmv_at_risk for m in metrics)
    weighted_p95 = (
        sum(m.latency_p95 * m.volume for m in metrics) / volume if volume else 0.0
    )
    return {
        "cohorts": [m.cohort_key for m in metrics],
        "volume": volume,
        "failures": failures,
        "failure_rate": round(failures / volume, 6) if volume else 0.0,
        "infrastructure_side_rate": round(infrastructure / volume, 6) if volume else 0.0,
        "latency_p95_weighted_mean": round(weighted_p95, 2),
        "gmv_total": round(gmv_total, 2),
        "gmv_at_risk": round(gmv_at_risk, 2),
    }


def _leading_confounder(
    detection: DetectionResult, subject: AnomalyCandidate
) -> AnomalyCandidate | None:
    """
    The strongest single-dimension anomaly on a DIFFERENT dimension from the subject.

    Restricted to depth-1 cohorts so the exclusion is a clean, interpretable population
    ("everything except gateway_C") rather than an intersection that would carve the
    residual too thin to measure.
    """
    for candidate in detection.reported:
        if candidate.cohort_key == subject.cohort_key:
            continue
        if len(candidate.dimensions) != 1:
            continue
        if candidate.dimensions == subject.dimensions:
            continue
        return candidate
    return None


def _siblings(
    all_metrics: list[CohortMetrics], subject: AnomalyCandidate
) -> list[CohortMetrics]:
    """Cohorts on the same dimension set as the subject, excluding the subject itself."""
    return [
        metric
        for metric in all_metrics
        if metric.dimensions == subject.dimensions and metric.cohort_key != subject.cohort_key
    ]


def build_evidence(
    detection: DetectionResult,
    subject_limit: int = EVIDENCE_SUBJECT_LIMIT,
) -> EvidenceBundle:
    """
    Build the full evidence set for a detection result.

    Reuses the detection run's `MetricStore`, so this adds at most one query per subject
    dimension (the preceding-window comparison) rather than re-aggregating everything.
    """
    started = time.perf_counter()
    store = detection.store
    baseline_window = detection.baseline_window
    current_window = detection.current_window
    duration = current_window[1] - current_window[0]
    prior_window = (current_window[0] - duration, current_window[0])

    subjects = list(detection.reported[:subject_limit])

    # Every dimension that can anchor a hypothesis must get a full evidence workup, even
    # if its strongest cohort fell outside the top ranks. Otherwise a hypothesis scored
    # against absent evidence collects the scoring defaults -- which is how a candidate
    # with no supporting facts at all can end up outranking one that was actually
    # measured.
    seen = {subject.cohort_key for subject in subjects}
    for dimension in SINGLE_DIMENSIONS:
        on_dimension = [c for c in detection.reported if c.dimensions == (dimension,)]
        if not on_dimension:
            continue
        best = max(on_dimension, key=lambda c: c.score)
        if best.cohort_key not in seen:
            subjects.append(best)
            seen.add(best.cohort_key)

    records: list[EvidenceRecord] = []

    for subject in subjects:
        baseline = subject.baseline
        current = subject.current
        gateway_id = subject.cohort_definition.get("gateway")
        segment = {
            key: value for key, value in subject.cohort_definition.items() if key != "gateway"
        } or None

        # --- 1. failure rate: the headline movement -------------------------------
        records.append(
            EvidenceRecord(
                evidence_type=EVIDENCE_FAILURE_RATE,
                metric_name="failure_rate",
                cohort_key=subject.cohort_key,
                cohort_definition=subject.cohort_definition,
                gateway_id=gateway_id,
                segment=segment,
                baseline_value=baseline.failure_rate,
                current_value=current.failure_rate,
                delta=subject.absolute_delta,
                relative_delta=subject.relative_delta_value,
                significance_sigma=subject.significance_sigma,
                control_group=None,
                source_layer=current.source_layer,
                evidence_source="cohort_metrics:failure_rate",
                time_window_start=current_window[0],
                time_window_end=current_window[1],
                explanation=(
                    f"Failure rate for {subject.cohort_key} moved from "
                    f"{baseline.failure_rate:.4%} (baseline, n={baseline.volume:,}) to "
                    f"{current.failure_rate:.4%} (incident window, n={current.volume:,}), "
                    f"a {subject.absolute_delta:.4%} absolute increase at "
                    f"{subject.significance_sigma:.2f} sigma."
                ),
            )
        )

        # --- 2. latency: does the cohort also slow down? ---------------------------
        latency_delta = current.latency_p95 - baseline.latency_p95
        records.append(
            EvidenceRecord(
                evidence_type=EVIDENCE_LATENCY,
                metric_name="latency_p95",
                cohort_key=subject.cohort_key,
                cohort_definition=subject.cohort_definition,
                gateway_id=gateway_id,
                segment=segment,
                baseline_value=baseline.latency_p95,
                current_value=current.latency_p95,
                delta=latency_delta,
                relative_delta=(
                    latency_delta / baseline.latency_p95 if baseline.latency_p95 else 0.0
                ),
                significance_sigma=None,
                control_group=None,
                source_layer=current.source_layer,
                evidence_source="cohort_metrics:latency_p95",
                time_window_start=current_window[0],
                time_window_end=current_window[1],
                explanation=(
                    f"p95 latency for {subject.cohort_key} moved from "
                    f"{baseline.latency_p95:,.0f}ms to {current.latency_p95:,.0f}ms "
                    f"({latency_delta:+,.0f}ms)."
                ),
            )
        )

        # --- 3. response mix: issuer-side vs infrastructure-side -------------------
        # This is the metric that separates "the payment infrastructure degraded" from
        # "the issuer started declining", so it carries real diagnostic weight.
        infra_delta = current.infrastructure_side_rate - baseline.infrastructure_side_rate
        records.append(
            EvidenceRecord(
                evidence_type=EVIDENCE_RESPONSE_MIX,
                metric_name="infrastructure_side_rate",
                cohort_key=subject.cohort_key,
                cohort_definition=subject.cohort_definition,
                gateway_id=gateway_id,
                segment=segment,
                baseline_value=baseline.infrastructure_side_rate,
                current_value=current.infrastructure_side_rate,
                delta=infra_delta,
                relative_delta=relative_delta(
                    baseline.infrastructure_side_rate, current.infrastructure_side_rate
                ),
                significance_sigma=two_proportion_z(
                    baseline.infrastructure_side,
                    baseline.volume,
                    current.infrastructure_side,
                    current.volume,
                ),
                control_group=None,
                source_layer=current.source_layer,
                evidence_source="cohort_metrics:infrastructure_side_rate",
                time_window_start=current_window[0],
                time_window_end=current_window[1],
                explanation=(
                    f"Infrastructure-side responses (PROCESSING_ERROR, TIMEOUT) for "
                    f"{subject.cohort_key} moved from "
                    f"{baseline.infrastructure_side_rate:.4%} to "
                    f"{current.infrastructure_side_rate:.4%} ({infra_delta:+.4%})."
                ),
            )
        )

        # --- 4. control comparison: did the peers move too? -----------------------
        current_siblings = _siblings(store.metrics(subject.dimensions, current_window), subject)
        baseline_siblings = _siblings(
            store.metrics(subject.dimensions, baseline_window), subject
        )
        control_current = _aggregate(current_siblings)
        control_baseline = _aggregate(baseline_siblings)
        control_delta = control_current["failure_rate"] - control_baseline["failure_rate"]
        control_sigma = two_proportion_z(
            control_baseline["failures"],
            control_baseline["volume"],
            control_current["failures"],
            control_current["volume"],
        )
        records.append(
            EvidenceRecord(
                evidence_type=EVIDENCE_CONTROL_COMPARISON,
                metric_name="control_failure_rate",
                cohort_key=subject.cohort_key,
                cohort_definition=subject.cohort_definition,
                gateway_id=gateway_id,
                segment=segment,
                baseline_value=control_baseline["failure_rate"],
                current_value=control_current["failure_rate"],
                delta=control_delta,
                relative_delta=relative_delta(
                    control_baseline["failure_rate"], control_current["failure_rate"]
                ),
                significance_sigma=control_sigma,
                control_group={
                    "baseline": control_baseline,
                    "current": control_current,
                    "subject_excluded": subject.cohort_key,
                },
                source_layer=control_current.get("source_layer", current.source_layer),
                evidence_source="cohort_metrics:control_group_aggregate",
                time_window_start=current_window[0],
                time_window_end=current_window[1],
                explanation=(
                    f"Control group ({', '.join(control_current['cohorts']) or 'none'}) moved "
                    f"from {control_baseline['failure_rate']:.4%} to "
                    f"{control_current['failure_rate']:.4%} ({control_delta:+.4%}, "
                    f"{control_sigma:.2f} sigma) over the same window, while "
                    f"{subject.cohort_key} moved {subject.absolute_delta:+.4%}."
                ),
            )
        )

        # --- 5. blast radius: how much of the fleet is affected? ------------------
        # A localised failure and a systemic one produce the same headline rate move on
        # the affected cohort; only breadth tells them apart.
        sibling_baseline_index = {m.cohort_key: m for m in baseline_siblings}
        moved = 0
        comparable = 0
        for sibling in current_siblings:
            sibling_baseline = sibling_baseline_index.get(sibling.cohort_key)
            if sibling_baseline is None or sibling_baseline.volume == 0:
                continue
            comparable += 1
            if sibling.failure_rate - sibling_baseline.failure_rate >= MIN_ABSOLUTE_RATE_DELTA:
                moved += 1
        blast_radius = (moved + 1) / (comparable + 1) if comparable >= 0 else 1.0
        records.append(
            EvidenceRecord(
                evidence_type=EVIDENCE_BLAST_RADIUS,
                metric_name="blast_radius",
                cohort_key=subject.cohort_key,
                cohort_definition=subject.cohort_definition,
                gateway_id=gateway_id,
                segment=segment,
                baseline_value=None,
                current_value=blast_radius,
                delta=None,
                relative_delta=None,
                significance_sigma=None,
                control_group={"peers_moved": moved, "peers_comparable": comparable},
                source_layer=current.source_layer,
                evidence_source="cohort_metrics:blast_radius",
                time_window_start=current_window[0],
                time_window_end=current_window[1],
                explanation=(
                    f"{moved} of {comparable} peer cohorts on the same dimension also rose by "
                    f"at least {MIN_ABSOLUTE_RATE_DELTA:.2%}; including the subject, "
                    f"{blast_radius:.1%} of the dimension is affected."
                ),
            )
        )

        # --- 6. temporal alignment: is the change confined to this window? --------
        prior_index = {
            m.cohort_key: m for m in store.metrics(subject.dimensions, prior_window)
        }
        prior = prior_index.get(subject.cohort_key)
        if prior is not None and prior.volume > 0:
            prior_delta = current.failure_rate - prior.failure_rate
            records.append(
                EvidenceRecord(
                    evidence_type=EVIDENCE_TEMPORAL,
                    metric_name="failure_rate_vs_preceding_window",
                    cohort_key=subject.cohort_key,
                    cohort_definition=subject.cohort_definition,
                    gateway_id=gateway_id,
                    segment=segment,
                    baseline_value=prior.failure_rate,
                    current_value=current.failure_rate,
                    delta=prior_delta,
                    relative_delta=relative_delta(prior.failure_rate, current.failure_rate),
                    significance_sigma=two_proportion_z(
                        prior.failures, prior.volume, current.failures, current.volume
                    ),
                    control_group=None,
                    source_layer=current.source_layer,
                    evidence_source="cohort_metrics:preceding_window",
                    time_window_start=prior_window[0],
                    time_window_end=prior_window[1],
                    explanation=(
                        f"In the equally-long window immediately before the incident, "
                        f"{subject.cohort_key} ran at {prior.failure_rate:.4%} "
                        f"(n={prior.volume:,}); the change is confined to the incident "
                        f"window rather than a pre-existing drift."
                    ),
                )
            )

        # --- 7. confounding check: is this cohort moving on its own? --------------
        # A degraded gateway drags every dimension that intersects it -- the regions,
        # networks and banks that happen to route through it all look mildly anomalous.
        # Removing the leading suspect on another dimension separates a genuine cause
        # from its shadow: a real cause survives the removal, a shadow collapses.
        confounder = _leading_confounder(detection, subject)
        if confounder is not None:
            exclusion = dict(confounder.cohort_definition)
            residual_current = store.residual(
                subject.dimensions, current_window, exclusion
            ).get(subject.cohort_key)
            residual_baseline = store.residual(
                subject.dimensions, baseline_window, exclusion
            ).get(subject.cohort_key)

            if residual_current is not None and residual_baseline is not None:
                residual_delta = residual_current.failure_rate - residual_baseline.failure_rate
                independence = (
                    max(0.0, residual_delta / subject.absolute_delta)
                    if subject.absolute_delta > 0
                    else 0.0
                )
                records.append(
                    EvidenceRecord(
                        evidence_type=EVIDENCE_CONFOUNDING,
                        metric_name="independence_from_" + confounder.cohort_key,
                        cohort_key=subject.cohort_key,
                        cohort_definition=subject.cohort_definition,
                        gateway_id=gateway_id,
                        segment=segment,
                        baseline_value=residual_baseline.failure_rate,
                        current_value=residual_current.failure_rate,
                        delta=residual_delta,
                        relative_delta=independence,
                        significance_sigma=two_proportion_z(
                            residual_baseline.failures,
                            residual_baseline.volume,
                            residual_current.failures,
                            residual_current.volume,
                        ),
                        control_group={
                            "excluded": exclusion,
                            "residual_volume": residual_current.volume,
                            "independence": round(independence, 6),
                        },
                        source_layer=residual_current.source_layer,
                        evidence_source="cohort_metrics:residual_excluding_confounder",
                        time_window_start=current_window[0],
                        time_window_end=current_window[1],
                        explanation=(
                            f"With {confounder.cohort_key} excluded, {subject.cohort_key} moved "
                            f"{residual_baseline.failure_rate:.4%} -> "
                            f"{residual_current.failure_rate:.4%} ({residual_delta:+.4%}), "
                            f"retaining {independence:.0%} of its original "
                            f"{subject.absolute_delta:.4%} movement. "
                            + (
                                "The anomaly persists independently."
                                if independence >= INDEPENDENCE_COLLAPSE_THRESHOLD
                                else "The anomaly largely disappears, indicating it is a "
                                "side-effect of the excluded population rather than a "
                                "cause in its own right."
                            )
                        ),
                    )
                )

        # --- 8. GMV impact, from authoritative observed transaction amounts -------
        records.append(
            EvidenceRecord(
                evidence_type=EVIDENCE_GMV,
                metric_name="gmv_at_risk",
                cohort_key=subject.cohort_key,
                cohort_definition=subject.cohort_definition,
                gateway_id=gateway_id,
                segment=segment,
                baseline_value=baseline.gmv_at_risk,
                current_value=current.gmv_at_risk,
                delta=current.gmv_at_risk - baseline.gmv_at_risk,
                relative_delta=relative_delta(baseline.gmv_at_risk, current.gmv_at_risk),
                significance_sigma=None,
                control_group=None,
                # Amounts come from `transactions.amount`, which is observed fact even
                # though which transactions failed is modelled.
                source_layer=SOURCE_LAYER_OBSERVED,
                evidence_source="cohort_metrics:gmv_at_risk",
                time_window_start=current_window[0],
                time_window_end=current_window[1],
                explanation=(
                    f"GMV attached to failing transactions in {subject.cohort_key} during the "
                    f"window is {current.gmv_at_risk:,.2f} of {current.gmv_total:,.2f} total. "
                    f"Amounts are observed values from the canonical dataset; which "
                    f"transactions failed is modelled."
                ),
            )
        )

    return EvidenceBundle(
        records=records,
        subjects=subjects,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
    )
