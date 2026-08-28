"""
The deterministic policy gate. Thirteen explicit gates, fail-closed.

READ THE SIGNATURE FIRST
------------------------
`validate()` takes a persisted simulation row, an RCA row, a live world state, and the
observed alert role. It takes NO thresholds, NO weights, and NO override flag. Every
bound it applies is a module constant in `constants.py`. There is no argument a caller
can pass -- and in Day 4B, no field an agent can emit -- that moves any threshold.

WHY NO_ACTION IS GATED DIFFERENTLY
-----------------------------------
NO_ACTION changes nothing, so the gates that exist to bound a CHANGE (target health,
eligibility, traffic shift, concentration, benefit margin) have no subject, and the
gates that exist to justify INTERVENING (the Day 3 evidence quartet, alert role) are
beside the point -- doing nothing needs no evidentiary case.

If the evidence gates applied to NO_ACTION, weak evidence would BLOCK the safe option
and leave the system with nothing it could recommend. That would invert the intent: the
whole reason NO_ACTION is a first-class candidate is so the system has something honest
to say when the evidence is thin. So NO_ACTION is gated on simulation validity and
freshness only -- the two properties that make the baseline itself trustworthy.

WHY THE RISK SCORE IS NOT A GATE
---------------------------------
The aggregate risk score never appears below. Gates bind on individual measurable
constraints, so a comfortable aggregate can never wash out one unacceptable component.
This is the same structural lesson as Day 3's P1-2 fix, applied to risk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from aventum_counterfactual.constants import ACTION_NO_ACTION, STATUS_VALID
from aventum_counterfactual.fingerprint import compute_input_fingerprint
from aventum_counterfactual.source import WorldState

from . import POLICY_VERSION
from .constants import (
    ALERT_NOT_PRIMARY,
    ALLOWED_SEVERITIES,
    BENEFIT_BELOW_NO_ACTION_MARGIN,
    CONCENTRATION_EXCEEDS_BOUND,
    CONFIDENCE_BELOW_THRESHOLD,
    EVIDENCE_STRENGTH_BELOW_THRESHOLD,
    MAX_CONCENTRATION_AFTER,
    MAX_TRAFFIC_SHIFT_PERCENTAGE,
    MIN_CONFIDENCE,
    MIN_EVIDENCE_STRENGTH,
    MIN_SIGNIFICANCE_SIGMA,
    NO_ACTION_MARGIN,
    RCA_NOT_CONFIDENT,
    REQUIRED_ALERT_ROLE,
    REQUIRED_RCA_VERDICT,
    RESULT_BLOCKED,
    RESULT_PERMITTED,
    SEVERITY_BELOW_THRESHOLD,
    SIGNIFICANCE_BELOW_THRESHOLD,
    SIMULATION_INVALID,
    STALE_SIMULATION,
    TARGET_NOT_ELIGIBLE,
    TARGET_NOT_HEALTHY,
    TRAFFIC_SHIFT_EXCEEDS_BOUND,
)


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict, with the value it saw and the bound it applied."""

    name: str
    passed: bool
    reason_code: str | None
    observed: object
    required: object

    def as_dict(self) -> dict:
        return {
            "gate": self.name,
            "result": "PASS" if self.passed else "FAIL",
            "reason_code": self.reason_code,
            "observed": self.observed,
            "required": self.required,
        }


@dataclass
class PolicyDecision:
    """The complete, explainable outcome of a validation pass."""

    result: str
    gates: list[GateResult] = field(default_factory=list)
    policy_version: str = POLICY_VERSION

    @property
    def permitted(self) -> bool:
        return self.result == RESULT_PERMITTED

    @property
    def reason_codes(self) -> list[str]:
        return [g.reason_code for g in self.gates if not g.passed and g.reason_code]

    def as_dict(self) -> dict:
        return {
            "result": self.result,
            "policy_version": self.policy_version,
            "gates": [g.as_dict() for g in self.gates],
            "reason_codes": self.reason_codes,
            # Stated explicitly so a reader of a stored decision never has to wonder
            # whether an unmentioned capacity check silently passed.
            "capacity_gate": "ABSENT — no capacity telemetry exists; not evaluated",
            "risk_score_gate": "ABSENT — gates bind on individual components, not an aggregate",
        }

    def constraints_in_force(self) -> dict:
        """The thresholds applied, persisted onto the recommendation for auditability."""
        return {
            "required_rca_verdict": REQUIRED_RCA_VERDICT,
            "min_confidence": MIN_CONFIDENCE,
            "min_evidence_strength": MIN_EVIDENCE_STRENGTH,
            "min_significance_sigma": MIN_SIGNIFICANCE_SIGMA,
            "allowed_severities": list(ALLOWED_SEVERITIES),
            "required_alert_role": REQUIRED_ALERT_ROLE,
            "max_traffic_shift_percentage": MAX_TRAFFIC_SHIFT_PERCENTAGE,
            "max_concentration_after": MAX_CONCENTRATION_AFTER,
            "no_action_margin": NO_ACTION_MARGIN,
            "policy_version": POLICY_VERSION,
            "thresholds_are": "SYSTEM_OWNED — module constants, not parameters",
        }


def _gate(name, passed, code, observed, required) -> GateResult:
    return GateResult(
        name=name, passed=passed, reason_code=None if passed else code, observed=observed,
        required=required,
    )


def validate(
    simulation,
    rca: dict | None,
    world: WorldState,
    alert_role: str | None,
    *,
    now: datetime | None = None,
) -> PolicyDecision:
    """
    Run every applicable gate. All must pass; a single failure BLOCKS.

    `simulation` is the persisted `CounterfactualSimulation` row -- never a dict a caller
    assembled, so the numbers gated on are the numbers that were simulated and stored.
    """
    now = now or datetime.now(timezone.utc)
    gates: list[GateResult] = []

    # ---- gates that apply to EVERY candidate, including NO_ACTION -------------------
    gates.append(
        _gate(
            "simulation_status",
            simulation.status == STATUS_VALID,
            SIMULATION_INVALID,
            simulation.status,
            STATUS_VALID,
        )
    )

    # Freshness is re-DERIVED from the current world, never read from a status column.
    # A flag can be edited; a hash over the actual inputs cannot.
    current_fingerprint = compute_input_fingerprint(
        world, simulation.simulation_seed, simulation.policy_version
    )
    gates.append(
        _gate(
            "simulation_freshness",
            current_fingerprint == simulation.input_fingerprint,
            STALE_SIMULATION,
            current_fingerprint[:16] + "...",
            simulation.input_fingerprint[:16] + "...",
        )
    )

    if simulation.action_type == ACTION_NO_ACTION:
        # Doing nothing needs no evidentiary case and bounds no change. See the module
        # docstring: gating NO_ACTION on evidence would block the safe option exactly
        # when the evidence is weakest.
        result = RESULT_PERMITTED if all(g.passed for g in gates) else RESULT_BLOCKED
        return PolicyDecision(result=result, gates=gates)

    # ---- Day 3 evidence quartet — all four required TOGETHER (P1-2) ------------------
    verdict = (rca or {}).get("verdict")
    confidence = float((rca or {}).get("confidence") or 0.0)
    evidence_strength = float((rca or {}).get("evidence_strength") or 0.0)
    significance = float((rca or {}).get("significance_sigma") or 0.0)
    severity = (rca or {}).get("severity")

    gates.append(
        _gate("rca_verdict", verdict == REQUIRED_RCA_VERDICT, RCA_NOT_CONFIDENT, verdict,
              REQUIRED_RCA_VERDICT)
    )
    gates.append(
        _gate("rca_confidence", confidence >= MIN_CONFIDENCE, CONFIDENCE_BELOW_THRESHOLD,
              confidence, f">= {MIN_CONFIDENCE}")
    )
    gates.append(
        _gate("evidence_strength", evidence_strength >= MIN_EVIDENCE_STRENGTH,
              EVIDENCE_STRENGTH_BELOW_THRESHOLD, evidence_strength,
              f">= {MIN_EVIDENCE_STRENGTH}")
    )
    gates.append(
        _gate("significance_sigma", significance >= MIN_SIGNIFICANCE_SIGMA,
              SIGNIFICANCE_BELOW_THRESHOLD, significance, f">= {MIN_SIGNIFICANCE_SIGMA}")
    )
    gates.append(
        _gate("severity", severity in ALLOWED_SEVERITIES, SEVERITY_BELOW_THRESHOLD, severity,
              list(ALLOWED_SEVERITIES))
    )
    gates.append(
        _gate("alert_role", alert_role == REQUIRED_ALERT_ROLE, ALERT_NOT_PRIMARY, alert_role,
              REQUIRED_ALERT_ROLE)
    )

    # ---- target gates — re-read from the CURRENT world, not from the simulation ------
    target = simulation.target_gateway_id
    eligibility = world.eligibility.get(target)
    gates.append(
        _gate("target_eligible", bool(eligibility and eligibility.is_eligible),
              TARGET_NOT_ELIGIBLE, getattr(eligibility, "is_eligible", None), True)
    )
    healthy, health_reason = world.healthy_for_whole_window(target) if target else (False, "NO_TARGET")
    gates.append(
        _gate("target_healthy", healthy, TARGET_NOT_HEALTHY, health_reason,
              "HEALTHY across the whole window")
    )

    # ---- bounds ----------------------------------------------------------------------
    shift = float(simulation.traffic_percentage or 0.0)
    gates.append(
        _gate("traffic_shift", shift <= MAX_TRAFFIC_SHIFT_PERCENTAGE,
              TRAFFIC_SHIFT_EXCEEDS_BOUND, shift, f"<= {MAX_TRAFFIC_SHIFT_PERCENTAGE}")
    )
    concentration = float(simulation.concentration_after or 0.0)
    gates.append(
        _gate("post_action_concentration", concentration <= MAX_CONCENTRATION_AFTER,
              CONCENTRATION_EXCEEDS_BOUND, concentration, f"<= {MAX_CONCENTRATION_AFTER}")
    )
    benefit = float(simulation.projected_gmv_retained or 0.0)
    gates.append(
        _gate("expected_benefit", benefit >= NO_ACTION_MARGIN, BENEFIT_BELOW_NO_ACTION_MARGIN,
              benefit, f">= {NO_ACTION_MARGIN}")
    )

    result = RESULT_PERMITTED if all(g.passed for g in gates) else RESULT_BLOCKED
    return PolicyDecision(result=result, gates=gates)
