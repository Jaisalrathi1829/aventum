"""
Explicit failure semantics.

Every failure in this package is a named exception or a typed outcome, never a silent
fallback and never a fabricated value. That is the whole design rule: when the agent
cannot do something, the system says so and stops or degrades — it does not invent a
plausible number to keep going.
"""

from __future__ import annotations


class AgentError(RuntimeError):
    """Base for every agent-layer failure."""


class AgentUnavailable(AgentError):
    """
    Ollama or the model could not be reached.

    NOT a degraded-quality path: the deterministic spine still produces a full
    recommendation, with `rationale = NULL`. Nothing is invented to paper over it.
    """


class ModelOutputInvalid(AgentError):
    """
    The model emitted something that is not a schema-valid decision.

    Deliberately fatal to the turn rather than repairable. Silently "fixing" unsafe
    model output is how an invalid decision becomes an executed one; the loop records
    the rejection and either retries cleanly or terminates.
    """


class ToolNotPermitted(AgentError):
    """
    The model asked for a tool that is not allowed from its current state.

    Covers both an unknown tool name and a real tool requested out of sequence — e.g.
    asking for approval before any recommendation exists.
    """


class ToolArgumentInvalid(AgentError):
    """Arguments failed schema or referential validation before dispatch."""


class ForbiddenNumericField(ToolArgumentInvalid):
    """
    The model tried to supply a quantitative value through a tool that accepts none.

    Its own exception type because this is the single most important thing the agent
    layer must refuse: it is the fabrication attempt that Day 4A's architecture exists
    to make structurally impossible.
    """


class BudgetExceeded(AgentError):
    """A turn, tool-call, simulation, or wall-clock budget ran out."""


class AgentLoopDetected(AgentError):
    """The same tool call repeated without new information — looping, not reasoning."""
