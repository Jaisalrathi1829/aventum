"""
Append-only audit emission.

THE ONLY WRITE VERB HERE IS INSERT
-----------------------------------
This module exposes `emit()` and nothing else. There is no update function, no delete
function, and no "correct the last event" helper -- so an audit trail cannot be revised
by any Day 4 code path. A retry adds an event; a rejection adds an event; a duplicate
suppression adds an event. History only grows.

WHAT AN EVENT MAY AND MAY NOT CONTAIN
--------------------------------------
`input_ref` and `output_ref` are `{table, id}` POINTERS, not copies of the rows. Copying
would let the audit trail drift out of sync with the data it describes; a pointer stays
correct because it resolves against the live row.

`payload` is a structured summary. It must never contain chain-of-thought. In Day 4A
that is trivially satisfied -- no model runs at all -- and in Day 4B it is satisfied
structurally, because `think:false` means no chain-of-thought is produced to store.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from aventum_policy import POLICY_VERSION

from . import ACTION_MODEL_VERSION
from .models import AuditEvent

# --------------------------------------------------------------- event vocabulary
SIMULATION_COMPLETED = "SIMULATION_COMPLETED"
SIMULATION_INVALID = "SIMULATION_INVALID"
POLICY_VALIDATED = "POLICY_VALIDATED"
RECOMMENDATION_CREATED = "RECOMMENDATION_CREATED"
RECOMMENDATION_BLOCKED = "RECOMMENDATION_BLOCKED"
APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
APPROVAL_DECIDED = "APPROVAL_DECIDED"
APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
ACTION_EXECUTED = "ACTION_EXECUTED"
ACTION_REJECTED = "ACTION_REJECTED"
ACTION_DUPLICATE_SUPPRESSED = "ACTION_DUPLICATE_SUPPRESSED"
ACTION_ROLLED_BACK = "ACTION_ROLLED_BACK"

# Reserved for Day 4B. Listed here so the vocabulary is complete and a reviewer can see
# what the agent layer will emit, but nothing in Day 4A produces them.
AGENT_RUN_STARTED = "AGENT_RUN_STARTED"
TOOL_CALLED = "TOOL_CALLED"
SUSPECTED_PROMPT_INJECTION = "SUSPECTED_PROMPT_INJECTION"

EVENT_TYPES = (
    ACTION_DUPLICATE_SUPPRESSED,
    ACTION_EXECUTED,
    ACTION_REJECTED,
    ACTION_ROLLED_BACK,
    AGENT_RUN_STARTED,
    APPROVAL_DECIDED,
    APPROVAL_EXPIRED,
    APPROVAL_REQUESTED,
    POLICY_VALIDATED,
    RECOMMENDATION_BLOCKED,
    RECOMMENDATION_CREATED,
    SIMULATION_COMPLETED,
    SIMULATION_INVALID,
    SUSPECTED_PROMPT_INJECTION,
    TOOL_CALLED,
)

# Actors. Day 4A only ever emits SYSTEM and HUMAN:<identity>; AGENT is reserved for 4B.
ACTOR_SYSTEM = "SYSTEM"
ACTOR_AGENT = "AGENT"


def human_actor(identity: str) -> str:
    return f"HUMAN:{identity}"


def emit(
    session: Session,
    *,
    event_type: str,
    actor: str,
    incident_id: int | None = None,
    input_ref: dict | None = None,
    output_ref: dict | None = None,
    payload: dict | None = None,
    fingerprint: str | None = None,
) -> AuditEvent:
    """
    Append one audit event. The only write path this module offers.

    Deliberately never raises on an unknown `event_type`: refusing to record something
    that happened, because its label was unexpected, would lose history to protect a
    vocabulary. The database CHECK guards the fields that must be well-formed (non-empty
    actor and type) and the vocabulary above documents the intended set.
    """
    event = AuditEvent(
        incident_id=incident_id,
        event_type=event_type,
        actor=actor,
        input_ref=input_ref,
        output_ref=output_ref,
        payload=payload,
        model_version=ACTION_MODEL_VERSION,
        policy_version=POLICY_VERSION,
        fingerprint=fingerprint,
    )
    session.add(event)
    session.flush()
    return event


def ref(table: str, row_id: int | None) -> dict:
    """A `{table, id}` pointer. Never a copy of the row."""
    return {"table": table, "id": row_id}
