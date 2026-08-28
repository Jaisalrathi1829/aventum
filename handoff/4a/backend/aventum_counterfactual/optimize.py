"""
Deterministic candidate sweep and selection.

NO_ACTION IS SIMULATED FIRST, ALWAYS, AS A REAL ROW
----------------------------------------------------
Not a null, not an implied zero, not a special case skipped for speed -- a full
simulation over the same cohort with the same machinery, persisted like any other
candidate. Two things follow. Every intervention is compared against a MEASURED baseline
rather than an assumed one; and if the sweep produces nothing better, the system already
holds a complete, citable simulation supporting the decision to do nothing.

THE SELECTION RULE IS ORDERED, AND ITS ORDER IS THE POLICY
-----------------------------------------------------------
    1. maximise expected GMV retained                    (primary)
    2. maximise expected success-rate delta              (secondary, tie-break)
    3. prefer the SMALLEST traffic shift reaching ≥95%   (least-intervention rule)
       of the best candidate's benefit

Step 3 is the one worth defending. Without it the optimiser always reaches for the
largest permitted shift, because more traffic moved is almost always marginally more GMV
retained. With it, a 10% reroute capturing 96% of a 30% reroute's benefit wins -- the
system prefers the least intervention that captures nearly all the value, which is what
a cautious operator would choose and what keeps concentration low for free.

NO STRUCTURAL BIAS TOWARD ACTING
--------------------------------
A candidate must beat NO_ACTION by at least `NO_ACTION_MARGIN` to be selected at all.
Below that, NO_ACTION wins and that is a SUCCESSFUL outcome, not a degraded one.
Invalid candidates are excluded from ranking entirely; a `SIMULATION_INVALID` row can
never become a recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .constants import (
    ACTION_NO_ACTION,
    ACTION_REROUTE,
    CANDIDATE_TRAFFIC_PERCENTAGES,
    STATUS_VALID,
)
from .models import CounterfactualSimulation
from .simulator import Candidate, run_counterfactual
from .source import WorldState

# A candidate must retain at least this much GMV MORE than NO_ACTION to be worth doing.
# Expressed in the same INR units as `transactions.amount`.
#
# Rationale for the value: the flagship affected cohort projects roughly 9,000 INR of
# expected GMV retained at a 20% reroute, so 1,000 INR is about 11% of a real
# intervention's benefit -- comfortably below a genuine win, comfortably above the
# numerical noise of a marginal one. It is a documented prototype constant, not a
# calibrated business threshold, and it is owned by the policy layer, which re-exports
# it; the simulator never decides whether a benefit is "enough".
DEFAULT_NO_ACTION_MARGIN = 1000.0

# Fraction of the best candidate's benefit a smaller shift must reach to displace it.
NEAR_BEST_FRACTION = 0.95


@dataclass(frozen=True)
class SweepResult:
    """Every evaluated candidate, plus the deterministic selection."""

    no_action: CounterfactualSimulation
    candidates: list[CounterfactualSimulation]
    best: CounterfactualSimulation
    selection_reason: str

    @property
    def valid_candidates(self) -> list[CounterfactualSimulation]:
        return [c for c in self.candidates if c.status == STATUS_VALID]

    @property
    def chose_no_action(self) -> bool:
        return self.best.action_type == ACTION_NO_ACTION

    def alternatives(self) -> list[dict]:
        """
        Every option that was NOT selected, with why -- for the approval payload.

        A human approving an intervention should see what else was considered and the
        specific reason each was passed over; "trust the optimiser" is not a decision
        input.
        """
        out = []
        for sim in [self.no_action] + self.candidates:
            if sim.simulation_id == self.best.simulation_id:
                continue
            if sim.status != STATUS_VALID:
                why = f"SIMULATION_INVALID: {sim.invalid_reason}"
            else:
                why = (
                    f"expected GMV retained {float(sim.projected_gmv_retained or 0):.2f} "
                    f"vs selected {float(self.best.projected_gmv_retained or 0):.2f}"
                )
            out.append(
                {
                    "candidate": sim.candidate_key,
                    "simulation_id": sim.simulation_id,
                    "status": sim.status,
                    "why_not_selected": why,
                }
            )
        return out


def build_candidate_set(world: WorldState) -> list[Candidate]:
    """
    Bounded reroutes to every eligible gateway other than the incident's own.

    Sorted by (gateway_id, percentage) so the sweep order is reproducible: an unordered
    set would still simulate identically per candidate, but the persisted insertion
    order -- and therefore simulation_ids -- would vary between runs.
    """
    source = world.affected_gateway_id
    targets = sorted(
        gid
        for gid, elig in world.eligibility.items()
        if elig.is_eligible and gid != source and gid in world.profiles
    )
    return [
        Candidate(
            action_type=ACTION_REROUTE,
            target_gateway_id=target,
            traffic_percentage=pct,
            source_gateway_id=source,
        )
        for target in targets
        for pct in CANDIDATE_TRAFFIC_PERCENTAGES
    ]


def _benefit(sim: CounterfactualSimulation) -> float:
    return float(sim.projected_gmv_retained or 0.0)


def _success_delta(sim: CounterfactualSimulation) -> float:
    return float(sim.expected_success_delta or 0.0)


def _shift(sim: CounterfactualSimulation) -> float:
    return float(sim.traffic_percentage or 0.0)


def select_best(
    no_action: CounterfactualSimulation,
    candidates: list[CounterfactualSimulation],
    no_action_margin: float = DEFAULT_NO_ACTION_MARGIN,
) -> tuple[CounterfactualSimulation, str]:
    """
    Apply the ordered selection rule. Pure and deterministic: no DB, no clock, no RNG.
    """
    valid = [c for c in candidates if c.status == STATUS_VALID]
    if not valid:
        return no_action, (
            "NO_ACTION selected: no candidate produced a valid controlled counterfactual"
        )

    # Primary, then secondary, then smallest shift -- the full sort key, so ties at every
    # level resolve deterministically rather than by list order.
    ranked = sorted(valid, key=lambda c: (-_benefit(c), -_success_delta(c), _shift(c), c.candidate_key))
    best = ranked[0]

    if _benefit(best) < no_action_margin:
        return no_action, (
            f"NO_ACTION selected: best candidate {best.candidate_key} retains "
            f"{_benefit(best):.2f}, below the {no_action_margin:.2f} margin required to act"
        )

    # Least-intervention rule: among candidates reaching ≥95% of the best benefit,
    # prefer the smallest traffic shift.
    threshold = _benefit(best) * NEAR_BEST_FRACTION
    near_best = [c for c in ranked if _benefit(c) >= threshold]
    chosen = sorted(near_best, key=lambda c: (_shift(c), -_benefit(c), c.candidate_key))[0]

    if chosen.simulation_id != best.simulation_id:
        reason = (
            f"{chosen.candidate_key} selected: retains {_benefit(chosen):.2f} "
            f"({_benefit(chosen) / _benefit(best):.1%} of the best candidate "
            f"{best.candidate_key}) at a smaller {_shift(chosen):.1f}% shift"
        )
    else:
        reason = (
            f"{chosen.candidate_key} selected: highest expected GMV retained "
            f"{_benefit(chosen):.2f}, exceeding the NO_ACTION margin of {no_action_margin:.2f}"
        )
    return chosen, reason


def run_candidate_sweep(
    session: Session,
    world: WorldState,
    analysis_run_id: int,
    no_action_margin: float = DEFAULT_NO_ACTION_MARGIN,
) -> SweepResult:
    """
    Simulate NO_ACTION and every bounded alternative, then select deterministically.

    Complexity is O(candidates × cohort), linear in both -- the sweep is a fixed small
    set (gateways × 3 percentages) over one pre-loaded cohort, with no query inside
    either loop. The world is read ONCE by the caller and shared, so every candidate is
    evaluated against an identical world.
    """
    no_action = run_counterfactual(
        session, world, analysis_run_id, Candidate(action_type=ACTION_NO_ACTION)
    )
    candidates = [
        run_counterfactual(session, world, analysis_run_id, candidate)
        for candidate in build_candidate_set(world)
    ]
    best, reason = select_best(no_action, candidates, no_action_margin)
    return SweepResult(
        no_action=no_action, candidates=candidates, best=best, selection_reason=reason
    )
