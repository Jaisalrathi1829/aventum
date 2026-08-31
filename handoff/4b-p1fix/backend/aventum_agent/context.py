"""
Deterministic context construction.

WHAT THE MODEL RECEIVES, AND WHY IT IS SMALL
---------------------------------------------
The model gets a compact, schema-shaped summary assembled from Day 3's handoff: the
incident, the PRIMARY detection, the RCA conclusion with all four Day 3 P1-2 signals
kept separate, a bounded slice of evidence, and per-gateway health. That is a few KB —
nowhere near the 250,000-row dataset, and comfortably inside the 8,000-token budget.

WHAT IT NEVER RECEIVES
-----------------------
  * ground truth — `build_handoff` does not expose it and this module never queries it
  * raw transactions, SQL, ORM sessions, credentials, connection strings
  * derivative alerts presented as equal-priority causes — they are carried in a
    separate, explicitly-labelled field so a causal shadow cannot be mistaken for an
    independent root cause (Day 3's P1-1 fix, preserved at the agent boundary)

DETERMINISM
-----------
Same incident state ⇒ byte-identical context. Evidence is ranked by an explicit stable
key and truncated at a fixed count — never sampled, never ordered by database natural
order. This matters because the context feeds the agent-run fingerprint: a context that
varied run to run would make replay comparison meaningless.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from aventum_counterfactual.constants import CAPACITY_UNAVAILABLE, ELIGIBILITY_UNCONDITIONAL
from aventum_counterfactual.source import WorldState
from aventum_incident.handoff import build_handoff

from .constants import (
    MAX_ALTERNATIVES_IN_CONTEXT,
    MAX_EVIDENCE_RECORDS,
    MAX_GATEWAYS_IN_CONTEXT,
)

# Rough chars-per-token for budget accounting. Used only to ENFORCE a ceiling, never to
# report a token count as if measured — real counts come from Ollama's `prompt_eval_count`.
# Calibrated against Ollama's own `prompt_eval_count`, not assumed. The flagship
# prompt measures 19,536 characters and 6,572 real tokens -- 2.97 chars/token, because
# JSON punctuation and gateway identifiers tokenize far denser than prose. The usual
# 4 was under-counting by ~40%, which made `MAX_CONTEXT_TOKENS` enforce a smaller
# number than it advertised. 3 keeps the estimate on the conservative side of measured.
_CHARS_PER_TOKEN = 3


@dataclass
class AgentContext:
    """The compact, deterministic view of one incident given to the model."""

    incident_id: int
    analysis_run_id: int
    payload: dict
    valid_evidence_ids: frozenset[int] = field(default_factory=frozenset)
    # Candidates offered IN THE CONTEXT are citable exactly like tool-returned ones.
    # They were produced by the deterministic simulator and handed to the model, so
    # citing them is grounded; treating them as fabricated (as an earlier version
    # did) rejects the model for using precisely what it was given.
    valid_simulation_ids: frozenset[int] = field(default_factory=frozenset)
    context_fingerprint: str = ""

    def as_json(self) -> str:
        # sort_keys makes the serialization order-independent and therefore stable.
        return json.dumps(self.payload, indent=None, sort_keys=True, default=str)

    def estimated_tokens(self) -> int:
        """
        Conservative estimate for budget enforcement. Not a measurement.

        Deliberately errs high: it exists so the loop stops at its OWN budget with a
        clean CONTEXT_EXCEEDED rather than letting the transport-layer truncation guard
        fire, which is a hard failure. See `_CHARS_PER_TOKEN` for the calibration.
        """
        return len(self.as_json()) // _CHARS_PER_TOKEN


def _evidence_sort_key(record: dict) -> tuple:
    """
    Stable ranking: strongest signal first, ties broken by id.

    Deterministic by construction — `significance_sigma` descending then `evidence_id`
    ascending is a total order over the records, so truncation always keeps the same
    subset for the same inputs.
    """
    sigma = record.get("significance_sigma")
    return (-(float(sigma) if sigma is not None else 0.0), int(record["evidence_id"]))


def build_agent_context(
    session: Session,
    incident_id: int,
    analysis_run_id: int,
    world: WorldState | None = None,
    candidates: list | None = None,
) -> AgentContext:
    """
    Assemble the initial context. Reads Day 3 through `build_handoff` and nothing else.

    Note there is no `session.execute(text(...))` anywhere in this function: the agent
    layer does not author SQL, so there is no query for a prompt to influence.
    """
    handoff = build_handoff(session, analysis_run_id)

    incident = handoff.incident
    rca = handoff.rca

    # PRIMARY only. Derivatives travel separately and are labelled.
    primary = [
        {
            "anomaly_id": d.anomaly_id,
            "alert_role": d.alert_role,
            "cohort_key": d.cohort_key,
            "severity": d.severity,
            "significance_sigma": round(d.significance_sigma, 4),
            "affected_population": d.affected_population,
            "gmv_at_risk": round(d.gmv_at_risk, 2),
            "rank": d.rank,
        }
        for d in handoff.detections
    ]

    evidence_records = [
        {
            "evidence_id": e.evidence_id,
            "evidence_type": e.evidence_type,
            "metric": e.metric,
            "baseline": e.baseline,
            "current": e.current,
            "delta": e.delta,
            "significance_sigma": e.significance_sigma,
            "cohort": e.cohort,
            "source_layer": e.source_layer,
            # Free text from the evidence engine. UNTRUSTED at the model boundary —
            # see tools.py for how tool results are framed as data.
            "explanation": e.explanation,
        }
        for e in handoff.evidence
    ]
    evidence_records.sort(key=_evidence_sort_key)
    selected_evidence = evidence_records[:MAX_EVIDENCE_RECORDS]

    # Routing options are pre-loaded rather than left for the agent to fetch.
    #
    # Same principle as pre-simulating the NO_ACTION baseline: this is deterministic,
    # read-only, side-effect-free data the agent invariably needs before it can reason
    # at all. Spending a model turn (~10 s on the target hardware) to retrieve facts the
    # system already holds is pure overhead, and it consumes budget that should go to
    # judgement. The agent still chooses the target and the bounded percentages — the
    # decision stays with the agent; only the lookup is done for it.
    gateways = []
    if world is not None:
        window_total = len(world.transactions) or 1
        share: dict[str, int] = {}
        for txn in world.transactions:
            share[txn.gateway_id] = share.get(txn.gateway_id, 0) + 1

        for gid in sorted(world.profiles)[:MAX_GATEWAYS_IN_CONTEXT]:
            healthy, reason = world.healthy_for_whole_window(gid)
            eligibility = world.eligibility.get(gid)
            is_eligible = bool(eligibility and eligibility.is_eligible)
            gateways.append(
                {
                    "gateway_id": gid,
                    "health_state": reason,
                    "healthy_whole_window": healthy,
                    "is_eligible": is_eligible,
                    "eligibility_basis": (
                        eligibility.basis if eligibility else ELIGIBILITY_UNCONDITIONAL
                    ),
                    "baseline_failure_probability": round(
                        world.profiles[gid].baseline_failure_probability, 6
                    ),
                    "current_traffic_share": round(share.get(gid, 0) / window_total, 6),
                    "viable_target": bool(
                        is_eligible and healthy and gid != world.affected_gateway_id
                    ),
                }
            )

        # Rank viable targets deterministically, best first.
        #
        # WHY THE SYSTEM RANKS INSTEAD OF THE MODEL
        # ------------------------------------------
        # Rule 2 of the system prompt forbids the model from computing, deriving, or
        # comparing figures — and "find the gateway with the lowest baseline failure
        # probability" is exactly such a computation. Leaving it to the model was an
        # inconsistency in this layer's own design, and in practice qwen3:8b did get it
        # wrong: it selected gateway_D (p=0.046164) over gateway_A (p=0.040197),
        # producing a recommendation worth ~3.4% less than the deterministic optimum.
        #
        # Ranking here restores the intended division of labour. The deterministic layer
        # supplies the ordering; the agent still decides whether to act at all, which
        # bounded percentage to use, and whether NO_ACTION is better. Judgement stays
        # with the agent, arithmetic stays with the system.
        viable = sorted(
            (g for g in gateways if g["viable_target"]),
            key=lambda g: (g["baseline_failure_probability"], g["gateway_id"]),
        )
        for rank, entry in enumerate(viable, start=1):
            entry["target_rank"] = rank
            entry["target_rank_basis"] = (
                "ranked by baseline_failure_probability ascending (1 = lowest modelled "
                "failure probability); computed deterministically, not by the model"
            )

    _reroute_available = any(
        c.action_type == "REROUTE" and c.status == "VALID"
        for c in (candidates or [])
    )

    payload = {
        "incident": (
            None
            if incident is None
            else {
                "incident_id": incident.incident_id,
                "incident_name": incident.incident_name,
                "incident_type": incident.incident_type,
                "affected_gateway": incident.affected_gateway,
                "affected_segment": incident.affected_segment,
                "window": {"start": incident.start, "end": incident.end},
                "status": incident.status,
                "provenance": incident.provenance,
            }
        ),
        "analysis_run_id": analysis_run_id,
        "primary_detections": primary,
        # Explicitly separated and explicitly explained, so the model cannot treat a
        # causal shadow as an independent cause.
        "derivative_detections_count": len(handoff.derivative_detections),
        "derivative_note": (
            "Derivative alerts are statistically real but causally explained by a "
            "PRIMARY alert. They are NOT independent root causes and must not be "
            "actioned as such."
        ),
        "rca": (
            None
            if rca is None
            else {
                "verdict": rca.verdict,
                "predicted_root_cause": rca.predicted_root_cause,
                "predicted_hypothesis_type": rca.predicted_hypothesis_type,
                "predicted_gateway_id": rca.predicted_gateway_id,
                "predicted_segment": rca.predicted_segment,
                # All four kept separate — collapsing them is the P1-2 defect.
                "confidence": rca.confidence,
                "evidence_strength": rca.evidence_strength,
                "significance_sigma": rca.significance_sigma,
                "severity": rca.severity,
                "summary": rca.summary,
                "supporting_evidence_ids": rca.supporting_evidence_ids,
                "contradicting_evidence_ids": rca.contradicting_evidence_ids,
                "affected_population": rca.affected_population,
                "control_population": rca.control_population,
                "alternatives_considered": rca.alternatives_considered[
                    :MAX_ALTERNATIVES_IN_CONTEXT
                ],
            }
        ),
        "evidence": selected_evidence,
        "evidence_truncated": len(evidence_records) > MAX_EVIDENCE_RECORDS,
        "evidence_total_available": len(evidence_records),
        # Deterministically pre-simulated candidates, each already persisted with a
        # real simulation_id. The agent SELECTS from these; it does not construct them.
        "candidates": [
            {
                "simulation_id": c.simulation_id,
                "candidate_key": c.candidate_key,
                "action_type": c.action_type,
                "target_gateway_id": c.target_gateway_id,
                "traffic_percentage": float(c.traffic_percentage or 0),
                "status": c.status,
                "invalid_reason": c.invalid_reason,
                "projected_gmv_retained": (
                    None if c.projected_gmv_retained is None
                    else float(c.projected_gmv_retained)
                ),
                "expected_success_delta": (
                    None if c.expected_success_delta is None
                    else float(c.expected_success_delta)
                ),
                "concentration_after": (
                    None if c.concentration_after is None else float(c.concentration_after)
                ),
                "risk_score": None if c.risk_score is None else float(c.risk_score),
            }
            for c in (candidates or [])
        ],
        "candidates_note": (
            "These candidates are already simulated and persisted. Select one by "
            "simulation_id. Do not invent percentages, targets, or figures. Request "
            "run_counterfactual only if you need a target these do not cover."
        ),
        # A FACT, not a rule.
        #
        # An earlier version put a conditional instruction here -- "if
        # rerouting_applicable is false ... answer NO_ACTION" -- and qwen3:8b applied
        # the consequent unconditionally: the flagship went from 10/10 REROUTE to 10/10
        # NO_ACTION on that change alone. An 8B model reading a conditional inside its
        # DATA does not reliably evaluate the condition.
        #
        # So the context now states only what is true, and states the opposite case
        # positively. Conditional guidance lives in the system prompt, where it belongs.
        "rerouting_applicable": _reroute_available,
        "rerouting_status": (
            "Routing intervention IS available for this incident. Valid reroute "
            "candidates are listed above; choose the best or justify NO_ACTION."
            if _reroute_available else
            "Routing intervention is NOT available for this incident: there is no "
            "affected gateway to move traffic away from, so no valid reroute candidate "
            "exists. NO_ACTION is the only coherent answer."
        ),
        "gateways": gateways,
        "best_target_note": (
            "Among gateways with viable_target true, target_rank 1 is the best "
            "available target, ranked deterministically by the system. Use it "
            "unless you have a stated reason not to."
        ),
        "honesty": {
            "capacity": CAPACITY_UNAVAILABLE,
            "eligibility_basis": ELIGIBILITY_UNCONDITIONAL,
            "provenance": "SYNTHETIC_INFRASTRUCTURE / SIMULATED_INCIDENT",
            "note": (
                "Amounts are observed; which transactions succeed is modelled. Any "
                "projection is a counterfactual estimate, never a realised result."
            ),
        },
    }

    valid_ids = frozenset(int(e["evidence_id"]) for e in evidence_records)
    valid_sims = frozenset(int(c.simulation_id) for c in (candidates or []))
    rendered = json.dumps(payload, sort_keys=True, default=str)
    fingerprint = hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    return AgentContext(
        incident_id=incident_id,
        analysis_run_id=analysis_run_id,
        payload=payload,
        valid_evidence_ids=valid_ids,
        valid_simulation_ids=valid_sims,
        context_fingerprint=fingerprint,
    )
