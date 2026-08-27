"""
The statistical primitives detection and evidence rely on.

Isolated in one small module so the maths can be unit-tested against hand-computed
values without a database, and so there is exactly one definition of "significance" in
the system rather than a slightly different z-score in each caller.
"""

from __future__ import annotations

import math

from .constants import ANOMALY_SCORE_REFERENCE_DELTA, SEVERITY_BANDS, SEVERITY_NONE


def two_proportion_z(
    baseline_successes: int,
    baseline_total: int,
    current_successes: int,
    current_total: int,
) -> float:
    """
    Pooled two-proportion z statistic for "did this rate really move?".

    Pooled (rather than unpooled) standard error is the standard choice when testing the
    null hypothesis that both samples share one underlying rate, which is exactly the
    question here. Returns 0.0 when either sample is empty or the pooled rate is
    degenerate -- a cohort with no data is not evidence of anything.

    "Successes" here means occurrences of the event being measured (in practice,
    failures); the naming is the statistical convention, not a claim about payments.
    """
    if baseline_total <= 0 or current_total <= 0:
        return 0.0

    pooled = (baseline_successes + current_successes) / (baseline_total + current_total)
    if pooled <= 0.0 or pooled >= 1.0:
        return 0.0

    standard_error = math.sqrt(pooled * (1.0 - pooled) * (1.0 / baseline_total + 1.0 / current_total))
    if standard_error <= 0.0:
        return 0.0

    baseline_rate = baseline_successes / baseline_total
    current_rate = current_successes / current_total
    return (current_rate - baseline_rate) / standard_error


def effect_factor(absolute_delta: float) -> float:
    """
    How much of a "fully significant" effect this rate change represents, in [0, 1].

    Linear up to ANOMALY_SCORE_REFERENCE_DELTA and clamped after, so the score stays
    interpretable: a cohort at or beyond the reference delta contributes its full
    statistical strength, and a cohort that barely moved contributes proportionally
    less no matter how many samples back it.
    """
    if absolute_delta <= 0:
        return 0.0
    return min(1.0, absolute_delta / ANOMALY_SCORE_REFERENCE_DELTA)


def anomaly_score(significance_sigma: float, absolute_delta: float) -> float:
    """
    Composite score used for ranking: statistical strength weighted by effect size.

    Deliberately not just sigma. Sigma alone rewards huge cohorts with trivial moves,
    which is the classic way a multi-dimensional detector produces confident nonsense.
    A negative sigma (the rate improved) scores 0 -- Day 3 detects degradation.
    """
    if significance_sigma <= 0:
        return 0.0
    return significance_sigma * effect_factor(absolute_delta)


def severity_for(significance_sigma: float) -> str:
    """Map a z score onto a severity band. Bands are ordered high to low."""
    for name, threshold in SEVERITY_BANDS:
        if significance_sigma >= threshold:
            return name
    return SEVERITY_NONE


def relative_delta(baseline_rate: float, current_rate: float) -> float:
    """
    Fractional change in rate. Returns 0.0 against a zero baseline rather than infinity,
    so a cohort that had no failures at all does not dominate every ranking.
    """
    if baseline_rate <= 0:
        return 0.0
    return (current_rate - baseline_rate) / baseline_rate
