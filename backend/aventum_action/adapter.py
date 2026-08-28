"""
The execution adapter boundary.

`RoutingActionAdapter` is the seam where a prototype becomes a product. Day 4 ships
exactly one implementation, `SimulatedRoutingAdapter`, which contacts nothing. A future
`LiveRoutingAdapter` would implement the same Protocol, and the recommendation, approval,
policy, and audit contracts would not change by a line -- that substitutability is the
whole reason the boundary is a Protocol rather than a function.

WHY THE RESULT IS MEASURED, NOT "SUCCESS"
------------------------------------------
An adapter that returned a bare success flag would make Day 5 impossible: verification
needs to compare what was projected against what actually (in simulation) happened, and
a boolean carries neither. So `ActionResult` carries the full post-action metric set --
allocation, success rate, failure rate, GMV, latency -- computed over the same cohort and
window the simulation used, so Day 5 can compare like with like.

Critically, the adapter RE-DERIVES those metrics from the projected outcomes rather than
echoing the simulation's own summary. Echoing would make `actual_simulated_outcome` a
copy of `expected_outcome` by construction, and the gap between them -- the only thing
Day 5 exists to measure -- would be identically zero and meaningless.

NOTHING HERE CLAIMS RECOVERY
----------------------------
The adapter reports what it modelled. Whether that constitutes recovery is Day 5's
judgement, and Day 4A must not pre-empt it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from aventum_counterfactual.constants import CAPACITY_UNAVAILABLE
from aventum_counterfactual.fingerprint import compute_execution_fingerprint

from . import SIMULATED_ADAPTER_NAME


@dataclass(frozen=True)
class ActionRequest:
    """What the executor hands an adapter. Contains no thresholds and no overrides."""

    recommendation_id: int
    approval_id: int
    incident_id: int
    simulation_id: int
    action_type: str
    source_gateway_id: str | None
    target_gateway_id: str | None
    traffic_percentage: float
    # The projected outcomes the simulation produced, replayed so the adapter can
    # measure rather than copy.
    projected_outcomes: list = field(default_factory=list)
    cohort_definition: dict = field(default_factory=dict)
    measurement_window: dict = field(default_factory=dict)
    current_distribution: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ActionResult:
    """A measurable simulated outcome. Never a bare status."""

    adapter_name: str
    applied: bool
    traffic_moved: int
    applied_source_gateway: str | None
    applied_target_gateway: str | None
    resulting_allocation: dict
    post_action_success_rate: float
    post_action_failure_rate: float
    post_action_failure_count: int
    post_action_gmv_total: float
    post_action_gmv_at_risk: float
    post_action_latency_p50: float
    post_action_latency_p95: float
    execution_fingerprint: str
    executed_at: datetime
    provenance: str
    notes: dict

    def as_dict(self) -> dict:
        return {
            "adapter_name": self.adapter_name,
            "applied": self.applied,
            "traffic_moved": self.traffic_moved,
            "applied_source_gateway": self.applied_source_gateway,
            "applied_target_gateway": self.applied_target_gateway,
            "resulting_allocation": self.resulting_allocation,
            "post_action_success_rate": round(self.post_action_success_rate, 6),
            "post_action_failure_rate": round(self.post_action_failure_rate, 6),
            "post_action_failure_count": self.post_action_failure_count,
            "post_action_gmv_total": round(self.post_action_gmv_total, 2),
            "post_action_gmv_at_risk": round(self.post_action_gmv_at_risk, 2),
            "post_action_latency_p50": round(self.post_action_latency_p50, 2),
            "post_action_latency_p95": round(self.post_action_latency_p95, 2),
            "execution_fingerprint": self.execution_fingerprint,
            "executed_at": self.executed_at.isoformat(),
            "provenance": self.provenance,
            "capacity": CAPACITY_UNAVAILABLE,
            "notes": self.notes,
        }


@runtime_checkable
class RoutingActionAdapter(Protocol):
    """
    The substitution seam. One method, so a live implementation has one thing to get right.
    """

    name: str

    def apply(self, action: ActionRequest) -> ActionResult:  # pragma: no cover - protocol
        ...


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    return sorted_values[int(fraction * (len(sorted_values) - 1))]


class SimulatedRoutingAdapter:
    """
    The only adapter that exists in Day 4. Contacts nothing.

    It applies the reroute to an in-memory allocation and measures the resulting
    modelled population. No network call, no external system, no credential, and no
    write to any prior-layer table.
    """

    name = SIMULATED_ADAPTER_NAME

    def apply(self, action: ActionRequest) -> ActionResult:
        outcomes = action.projected_outcomes
        moved = sum(1 for o in outcomes if o.rerouted)

        allocation = dict(action.current_distribution)
        if moved and action.action_type != "NO_ACTION":
            source, target = action.source_gateway_id, action.target_gateway_id
            allocation[source] = allocation.get(source, 0) - moved
            allocation[target] = allocation.get(target, 0) + moved

        total = len(outcomes)
        failures = sum(1 for o in outcomes if o.projected_status == "FAILED")
        successes = total - failures
        gmv_total = sum(o.amount for o in outcomes)
        gmv_at_risk = sum(o.amount for o in outcomes if o.projected_status == "FAILED")
        latencies = sorted(o.projected_latency_ms for o in outcomes)

        executed_at = datetime.now(timezone.utc)
        fingerprint = compute_execution_fingerprint(
            [
                str(action.recommendation_id),
                str(action.approval_id),
                str(action.simulation_id),
                self.name,
                str(moved),
                f"{successes}/{total}",
                f"{gmv_at_risk:.2f}",
            ]
        )

        return ActionResult(
            adapter_name=self.name,
            applied=True,
            traffic_moved=moved,
            applied_source_gateway=action.source_gateway_id,
            applied_target_gateway=action.target_gateway_id,
            resulting_allocation=allocation,
            post_action_success_rate=(successes / total) if total else 0.0,
            post_action_failure_rate=(failures / total) if total else 0.0,
            post_action_failure_count=failures,
            post_action_gmv_total=gmv_total,
            post_action_gmv_at_risk=gmv_at_risk,
            post_action_latency_p50=_percentile(latencies, 0.50),
            post_action_latency_p95=_percentile(latencies, 0.95),
            execution_fingerprint=fingerprint,
            executed_at=executed_at,
            provenance="SIMULATED_EXECUTION",
            notes={
                "no_real_infrastructure_contacted": True,
                "adapter": self.name,
                "measurement": (
                    "Re-derived from the projected outcome population, not copied from "
                    "the simulation summary — so expected and actual can genuinely differ."
                ),
                "recovery_claim": (
                    "NONE. This is a modelled post-action state. Whether it constitutes "
                    "recovery is Day 5's judgement, not this adapter's."
                ),
            },
        )
