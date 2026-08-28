"""
Aventum action layer (Day 4A): recommendation, human approval, simulated execution, audit.

THE STRUCTURAL PROPERTY THIS PACKAGE EXISTS TO GUARANTEE
--------------------------------------------------------
A fabricated number cannot enter a recommendation.

`build_recommendation()` accepts a `simulation_id` and no quantitative argument of any
kind. Every figure it persists is read server-side from that simulation row. This is not
a validation rule that checks a caller's number against the simulation -- it is the
absence of any parameter through which a number could arrive. A caller who wants a
different figure must first persist a different simulation, which is fingerprinted,
idempotent, and auditable.

That distinction matters most for Day 4B, where an LLM will author the `rationale` field
and nothing else. Instructing a model not to invent numbers is a prompt; giving it no
field to put one in is an architecture.

EXECUTION IS SIMULATED, AND THE DATABASE ENFORCES IT
-----------------------------------------------------
`actions.is_simulated` carries `CHECK (= true)`. Day 4 contacts no real payment
infrastructure, and PostgreSQL refuses to record an action claiming otherwise. Execution
never trusts an object it is handed: it re-reads persisted state and re-derives both the
input fingerprint and the full policy gate before the adapter is invoked.

Day 4A contains no Qwen, no Ollama client, no tool registry, and no agent loop.
"""

__version__ = "0.1.0"

# Bumped when recommendation/approval/execution semantics change structurally.
ACTION_MODEL_VERSION = "1.0.0"

# The only adapter that exists in Day 4. A future LiveRoutingAdapter would implement the
# same Protocol without changing the recommendation, approval, policy, or audit contracts.
SIMULATED_ADAPTER_NAME = "SimulatedRoutingAdapter"
