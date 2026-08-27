"""
Deterministic anomaly detection.

The detector is told a window and given a database. It is NOT told which gateway (or
bank, or method) is degraded, and it contains no reference to any particular cohort
value -- there is deliberately no `if gateway_id == "gateway_C"` anywhere in this
package. It scans every cohort that clears the minimum-sample bar, scores each by
comparing the incident window against the same cohort's own baseline, and ranks what
survives.

Alert discipline (docs/DAY3_IMPLEMENTATION_REPORT.md §Alerting Discipline):

  1. Minimum cohort size, in BOTH windows, before a cohort is scored at all.
  2. A minimum absolute rate move, so statistical significance on a trivial delta is
     not reported as an incident.
  3. A significance floor.
  4. Redundancy suppression: when a narrower cohort sits inside an already-reported
     broader one and adds no real strength, it is marked suppressed rather than
     emitted as a second alert. Without this, one gateway degradation produces an
     alert for every (gateway x bank x method) cell it happens to touch.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from .constants import (
    DUPLICATE_SUPPRESSION_SIGMA_MARGIN,
    INTERSECTION_DIMENSIONS,
    MIN_ABSOLUTE_RATE_DELTA,
    MIN_BASELINE_COHORT_SIZE,
    MIN_COHORT_SIZE,
    MIN_SIGNIFICANCE_SIGMA,
    SINGLE_DIMENSIONS,
)
from .metrics import CohortMetrics, MetricStore
from .statistics import anomaly_score, relative_delta, severity_for, two_proportion_z


@dataclass(frozen=True)
class DetectionConfig:
    """Detection policy. Every threshold is explicit and persisted with the run."""

    min_cohort_size: int = MIN_COHORT_SIZE
    min_baseline_cohort_size: int = MIN_BASELINE_COHORT_SIZE
    min_absolute_delta: float = MIN_ABSOLUTE_RATE_DELTA
    min_significance_sigma: float = MIN_SIGNIFICANCE_SIGMA
    suppression_margin: float = DUPLICATE_SUPPRESSION_SIGMA_MARGIN
    single_dimensions: tuple[str, ...] = SINGLE_DIMENSIONS
    intersections: tuple[tuple[str, ...], ...] = INTERSECTION_DIMENSIONS

    def as_dict(self) -> dict:
        return {
            "min_cohort_size": self.min_cohort_size,
            "min_baseline_cohort_size": self.min_baseline_cohort_size,
            "min_absolute_delta": self.min_absolute_delta,
            "min_significance_sigma": self.min_significance_sigma,
            "suppression_margin": self.suppression_margin,
            "single_dimensions": list(self.single_dimensions),
            "intersections": ["+".join(combo) for combo in self.intersections],
        }


@dataclass
class AnomalyCandidate:
    """One scored cohort. Mutable only so suppression can be stamped after ranking."""

    cohort_key: str
    cohort_definition: dict
    dimensions: tuple[str, ...]
    baseline: CohortMetrics
    current: CohortMetrics
    significance_sigma: float
    score: float
    severity: str
    absolute_delta: float
    relative_delta_value: float
    rank: int = 0
    suppressed: bool = False
    suppressed_by: str | None = None

    @property
    def depth(self) -> int:
        return max(len(self.dimensions), 1)


@dataclass(frozen=True)
class DetectionResult:
    candidates: list[AnomalyCandidate]
    cohorts_scanned: int
    population_baseline: CohortMetrics
    population_current: CohortMetrics
    elapsed_ms: float
    # Carried so the evidence engine can describe control groups and blast radius
    # without re-running a single aggregate.
    store: MetricStore
    baseline_window: tuple[datetime, datetime]
    current_window: tuple[datetime, datetime]

    @property
    def reported(self) -> list[AnomalyCandidate]:
        """Ranked, non-suppressed anomalies -- what an operator would actually see."""
        return [candidate for candidate in self.candidates if not candidate.suppressed]

    @property
    def top(self) -> AnomalyCandidate | None:
        reported = self.reported
        return reported[0] if reported else None


def _score_dimension(
    store: MetricStore,
    dimensions: tuple[str, ...],
    baseline_window: tuple[datetime, datetime],
    current_window: tuple[datetime, datetime],
    config: DetectionConfig,
) -> tuple[list[AnomalyCandidate], int]:
    """Score every cohort along one dimension set. Two queries, regardless of cardinality."""
    current_rows = store.metrics(dimensions, current_window)
    baseline_index = store.indexed(dimensions, baseline_window)
    candidates: list[AnomalyCandidate] = []
    scanned = 0

    for current in current_rows:
        baseline = baseline_index.get(current.cohort_key)
        if baseline is None:
            # A cohort that did not exist in the baseline has no comparison point. It is
            # not evidence of degradation, it is evidence of novelty.
            continue

        scanned += 1

        if current.volume < config.min_cohort_size:
            continue
        if baseline.volume < config.min_baseline_cohort_size:
            continue

        absolute = current.failure_rate - baseline.failure_rate
        if absolute < config.min_absolute_delta:
            continue

        sigma = two_proportion_z(
            baseline_successes=baseline.failures,
            baseline_total=baseline.volume,
            current_successes=current.failures,
            current_total=current.volume,
        )
        if sigma < config.min_significance_sigma:
            continue

        candidates.append(
            AnomalyCandidate(
                cohort_key=current.cohort_key,
                cohort_definition=current.cohort_definition,
                dimensions=dimensions,
                baseline=baseline,
                current=current,
                significance_sigma=sigma,
                score=anomaly_score(sigma, absolute),
                severity=severity_for(sigma),
                absolute_delta=absolute,
                relative_delta_value=relative_delta(baseline.failure_rate, current.failure_rate),
            )
        )

    return candidates, scanned


def _suppress_redundant(candidates: list[AnomalyCandidate], margin: float) -> None:
    """
    Mark narrower cohorts that a broader, already-reported cohort explains.

    A candidate is suppressed when some strictly-broader candidate (a subset of its
    dimension/value pairs) is already reported and the narrower one is not materially
    more significant. "Materially" is `margin` sigma -- a genuinely worse sub-segment
    still gets its own alert, which is what keeps a real bank-specific interaction
    visible inside a gateway incident.
    """
    # Broader (fewer dimensions) first, then strongest first, so a broad strong alert is
    # always available as a suppressor before narrower ones are considered.
    ordered = sorted(candidates, key=lambda c: (c.depth, -c.score))

    for index, candidate in enumerate(ordered):
        for broader in ordered[:index]:
            if broader.suppressed:
                continue
            if broader.depth >= candidate.depth:
                continue
            # Is `broader` a strict generalisation of `candidate`?
            if not all(
                candidate.cohort_definition.get(key) == value
                for key, value in broader.cohort_definition.items()
            ):
                continue
            if candidate.significance_sigma <= broader.significance_sigma + margin:
                candidate.suppressed = True
                candidate.suppressed_by = broader.cohort_key
                break


def detect_anomalies(
    session: Session,
    generation_run_id: int,
    baseline_window: tuple[datetime, datetime],
    current_window: tuple[datetime, datetime],
    incident_id: int | None = None,
    config: DetectionConfig | None = None,
    store: MetricStore | None = None,
) -> DetectionResult:
    """
    Scan every configured cohort and return a ranked anomaly set.

    Deterministic: the same inputs produce the same candidates in the same order.
    Ranking ties are broken by cohort_key so the output cannot depend on dictionary or
    database row ordering.
    """
    started = time.perf_counter()
    config = config or DetectionConfig()
    store = store or MetricStore(session, generation_run_id, incident_id)

    population_baseline = store.population(baseline_window)
    population_current = store.population(current_window)

    all_candidates: list[AnomalyCandidate] = []
    scanned_total = 0

    dimension_sets: list[tuple[str, ...]] = [(dim,) for dim in config.single_dimensions]
    dimension_sets.extend(config.intersections)

    for dimensions in dimension_sets:
        candidates, scanned = _score_dimension(
            store,
            dimensions=dimensions,
            baseline_window=baseline_window,
            current_window=current_window,
            config=config,
        )
        all_candidates.extend(candidates)
        scanned_total += scanned

    _suppress_redundant(all_candidates, config.suppression_margin)

    # Stable, fully-determined ordering: strongest score first, then sigma, then key.
    all_candidates.sort(key=lambda c: (-c.score, -c.significance_sigma, c.cohort_key))
    for position, candidate in enumerate(
        [c for c in all_candidates if not c.suppressed], start=1
    ):
        candidate.rank = position
    # Suppressed candidates are retained for audit, ranked after everything reported.
    suppressed_start = len([c for c in all_candidates if not c.suppressed]) + 1
    for offset, candidate in enumerate(
        [c for c in all_candidates if c.suppressed], start=suppressed_start
    ):
        candidate.rank = offset

    return DetectionResult(
        candidates=all_candidates,
        cohorts_scanned=scanned_total,
        population_baseline=population_baseline,
        population_current=population_current,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        store=store,
        baseline_window=baseline_window,
        current_window=current_window,
    )
