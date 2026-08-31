"""
The public agent entrypoint.

`analyze_incident()` is the whole external surface of Day 4B. It orchestrates:

    load world → simulate NO_ACTION baseline → build context → run the agent → report

and degrades cleanly when the model is unavailable.

WHY THE NO_ACTION BASELINE IS SIMULATED BEFORE THE MODEL RUNS
--------------------------------------------------------------
So that a real, measured baseline always exists even if the agent never asks for one.
Two things follow: the agent can never be in a position where "do nothing" has no
quantitative support, and a run that ends in NO_ACTION still cites a persisted
simulation rather than an assumption.

AGENT-UNAVAILABLE IS A FIRST-CLASS PATH, NOT AN ERROR PATH
-----------------------------------------------------------
If Ollama is down, `analyze_incident` returns `AGENT_UNAVAILABLE` and the caller falls
back to Day 4A's deterministic pipeline, which produces a complete recommendation with
`rationale = NULL`. Nothing is invented to fill the gap — no rationale, no confidence,
no recommendation the model did not actually make.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from aventum_action.audit import ACTOR_SYSTEM, emit, ref
from aventum_action.pipeline import DecisionResult, run_decision_pipeline
from aventum_counterfactual.constants import ACTION_NO_ACTION
from aventum_counterfactual.simulator import Candidate, run_counterfactual
from aventum_counterfactual.source import load_world_state

from .client import OllamaClient
from .constants import RUN_AGENT_UNAVAILABLE
from .context import build_agent_context
from .loop import AgentLoop, AgentOutcome


@dataclass
class AgentAnalysis:
    """What Day 4B returns to a caller (CLI, test, or Day 5)."""

    incident_id: int
    analysis_run_id: int
    outcome: AgentOutcome
    baseline_simulation_id: int | None
    agent_available: bool
    deterministic_fallback: DecisionResult | None = None

    @property
    def status(self) -> str:
        return self.outcome.status

    def summary(self) -> dict:
        return {
            "incident_id": self.incident_id,
            "analysis_run_id": self.analysis_run_id,
            "agent_available": self.agent_available,
            "status": self.outcome.status,
            "final_state": self.outcome.final_state,
            "agent_run_id": self.outcome.agent_run_id,
            "recommendation_id": self.outcome.recommendation_id,
            "approval_id": self.outcome.approval_id,
            "selected_simulation_id": self.outcome.selected_simulation_id,
            "baseline_simulation_id": self.baseline_simulation_id,
            "turns": len(self.outcome.turns),
            "tool_calls": self.outcome.tool_calls_used,
            "simulations": self.outcome.simulations_used,
            "context_tokens_estimate": self.outcome.context_tokens_estimate,
            "prompt_tokens_total": self.outcome.prompt_tokens_total,
            "output_tokens_total": self.outcome.output_tokens_total,
            "elapsed_ms": round(self.outcome.elapsed_ms, 1),
            "mean_qwen_latency_ms": (
                round(sum(self.outcome.qwen_latencies_ms) / len(self.outcome.qwen_latencies_ms), 1)
                if self.outcome.qwen_latencies_ms else None
            ),
            "agent_run_fingerprint": self.outcome.agent_run_fingerprint,
            "uncertainty": self.outcome.uncertainty,
            "error": self.outcome.error,
            "provenance": "SYNTHETIC_INCIDENT / SIMULATED_EXECUTION",
        }


def ensure_no_action_baseline(session: Session, world, analysis_run_id: int):
    """Simulate NO_ACTION so a measured baseline always exists. Idempotent."""
    return run_counterfactual(session, world, analysis_run_id,
                              Candidate(action_type=ACTION_NO_ACTION))


def analyze_incident(
    session: Session,
    incident_id: int,
    analysis_run_id: int,
    client: OllamaClient | None = None,
    deterministic_fallback: bool = True,
) -> AgentAnalysis:
    """
    Run the bounded agent over one incident.

    Stops at REQUEST_APPROVAL. There is no execution path from here — approving and
    executing are separate human-initiated operations in `aventum_action`.
    """
    client = client or OllamaClient()
    world = load_world_state(session, incident_id)
    baseline = ensure_no_action_baseline(session, world, analysis_run_id)

    if not client.is_available():
        emit(session, event_type="AGENT_UNAVAILABLE", actor=ACTOR_SYSTEM,
             incident_id=incident_id,
             payload={"model": client.model, "base_url": client.base_url,
                      "note": ("Ollama or the model is unreachable. The deterministic "
                               "spine remains authoritative; no rationale is invented.")})
        fallback = (
            run_decision_pipeline(session, incident_id, analysis_run_id)
            if deterministic_fallback else None
        )
        return AgentAnalysis(
            incident_id=incident_id, analysis_run_id=analysis_run_id,
            outcome=AgentOutcome(agent_run_id=None, status=RUN_AGENT_UNAVAILABLE,
                                 final_state="ABANDONED",
                                 error=f"{client.model} unavailable at {client.base_url}"),
            baseline_simulation_id=baseline.simulation_id,
            agent_available=False, deterministic_fallback=fallback,
        )

    context = build_agent_context(session, incident_id, analysis_run_id, world)
    outcome = AgentLoop(session, client, world, context,
                        incident_id, analysis_run_id).run()

    if outcome.recommendation_id is not None:
        emit(session, event_type="AGENT_RECOMMENDATION_LINKED", actor=ACTOR_SYSTEM,
             incident_id=incident_id,
             input_ref=ref("agent_runs", outcome.agent_run_id),
             output_ref=ref("recommendations", outcome.recommendation_id),
             payload={"selected_simulation_id": outcome.selected_simulation_id,
                      "final_state": outcome.final_state})

    return AgentAnalysis(
        incident_id=incident_id, analysis_run_id=analysis_run_id, outcome=outcome,
        baseline_simulation_id=baseline.simulation_id, agent_available=True,
    )
