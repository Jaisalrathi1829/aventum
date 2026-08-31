"""
Aventum agentic reasoning layer (Day 4B).

Qwen3 8B as a BOUNDED TOOL-USING AGENT sitting on top of the Day 4A deterministic spine.
It interprets and orchestrates. It never calculates.

THE AUTHORITY MODEL, RESTATED BECAUSE EVERY MODULE HERE DEPENDS ON IT
---------------------------------------------------------------------
    DETERMINISTIC SYSTEMS CALCULATE
      → QWEN INTERPRETS / ORCHESTRATES
        → POLICY CONSTRAINS
          → HUMAN APPROVES
            → EXECUTION ADAPTER ACTS

Qwen is never authoritative for quantitative truth. Every number it may state comes
from a tool result produced by Day 2B/Day 3/Day 4A code; every number that reaches a
recommendation is read server-side from a persisted simulation row.

WHY THIS PACKAGE CANNOT BREAK THE SPINE
----------------------------------------
Day 4A is complete and passes on its own with no model present. This package adds a
narrative and orchestration layer over it and holds no quantitative authority of its
own:

  * `propose_action` accepts no numeric field, so a fabricated figure has no parameter
    through which to enter a recommendation. That is Day 4A's guarantee, preserved.
  * The agent may only SELECT among simulations that already exist and are persisted.
    It cannot invent a new quantitative candidate — it can only ask the deterministic
    simulator to evaluate one, and the simulator decides what the numbers are.
  * There is no execution tool. Not a restricted one — none. The agent's authority ends
    at `request_human_approval`.
  * If Ollama is unavailable the run returns `AGENT_UNAVAILABLE` and the deterministic
    pipeline still produces a recommendation, with `rationale = NULL`.

MODEL OUTPUT IS DATA, NEVER CODE
---------------------------------
Nothing the model emits is ever executed. Its JSON passes through schema validation,
state validation, a tool allowlist, argument validation, and a typed dispatcher before
any Python function runs. There is no `eval`, no dynamic import, no SQL string built
from model output, and no shell path anywhere in this package.

Non-claims: this operates on a SYNTHETIC infrastructure model under a SIMULATED
incident. Nothing here touches real payment infrastructure, and no figure it produces
is real recovered GMV.
"""

__version__ = "0.1.0"

# Bumped when the agent architecture changes in a way that would alter decisions from
# identical inputs. Stamped into the agent-run fingerprint.
AGENT_MODEL_VERSION = "1.0.0"
