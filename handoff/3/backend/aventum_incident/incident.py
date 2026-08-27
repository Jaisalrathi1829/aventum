"""
Incident definition, identity, and lifecycle.

An incident is a declarative definition (what degrades, where, when, how hard) plus a
forward-only lifecycle. This module owns creating it idempotently and advancing its
state; it deliberately owns no generative logic -- that is `simulate.py`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import INCIDENT_CONFIG_VERSION, INCIDENT_MODEL_VERSION
from .constants import INCIDENT_STATUS_ORDER, INCIDENT_TYPES
from .models import Incident, IncidentGroundTruth


class IncidentDefinitionError(ValueError):
    """The incident definition is not internally valid."""


class IncidentLifecycleError(RuntimeError):
    """An illegal lifecycle transition was attempted."""


@dataclass(frozen=True)
class IncidentDefinition:
    """
    A complete, hashable incident specification.

    Everything that influences generated outcomes must appear here, because
    `incident_key` is a digest of exactly these fields: two definitions that differ in
    any way that matters must not collide, and two that are identical must resolve to
    the same stored incident.
    """

    incident_name: str
    incident_type: str
    incident_start: datetime
    incident_end: datetime
    failure_multiplier: float
    latency_multiplier: float
    timeout_multiplier: float
    generation_run_id: int
    source_ingestion_run_id: int
    incident_seed: str
    affected_gateway_id: str | None = None
    affected_segment: dict | None = None
    target_failure_rate: float | None = None
    # Ground truth travels with the definition but is stored in its own isolated table.
    ground_truth_root_cause: str = ""
    ground_truth_detail: dict | None = field(default=None)
    notes: str | None = None

    def validate(self) -> None:
        if self.incident_type not in INCIDENT_TYPES:
            raise IncidentDefinitionError(
                f"unknown incident_type {self.incident_type!r}; "
                f"expected one of {sorted(INCIDENT_TYPES)}"
            )
        # Awareness is checked FIRST: comparing a naive datetime with an aware one
        # raises TypeError, which would mask this error with a confusing traceback.
        if self.incident_start.tzinfo is None or self.incident_end.tzinfo is None:
            raise IncidentDefinitionError(
                "incident_start and incident_end must be timezone-aware; a naive "
                "timestamp would compare unpredictably against timestamptz columns"
            )
        if self.incident_end <= self.incident_start:
            raise IncidentDefinitionError(
                "incident_end must be strictly after incident_start "
                f"(got start={self.incident_start!r}, end={self.incident_end!r}); "
                "a zero-width or inverted window has no transactions and no meaning"
            )
        for name, value in (
            ("failure_multiplier", self.failure_multiplier),
            ("latency_multiplier", self.latency_multiplier),
            ("timeout_multiplier", self.timeout_multiplier),
        ):
            if value <= 0:
                raise IncidentDefinitionError(f"{name} must be > 0, got {value}")
        if not self.incident_seed:
            raise IncidentDefinitionError("incident_seed must be a non-empty string")
        if self.incident_type == "gateway_degradation" and not self.affected_gateway_id:
            raise IncidentDefinitionError(
                "a gateway_degradation incident requires affected_gateway_id"
            )

    def identity_payload(self) -> dict:
        """
        The exact, ordered content hashed into `incident_key`.

        Timestamps are rendered as UTC ISO-8601 so an equivalent instant expressed in a
        different timezone yields the same key -- the window is a set of instants, not
        a string.
        """
        return {
            "incident_name": self.incident_name,
            "incident_type": self.incident_type,
            "affected_gateway_id": self.affected_gateway_id,
            "affected_segment": self.affected_segment,
            "incident_start": _utc_iso(self.incident_start),
            "incident_end": _utc_iso(self.incident_end),
            "failure_multiplier": round(float(self.failure_multiplier), 6),
            "latency_multiplier": round(float(self.latency_multiplier), 6),
            "timeout_multiplier": round(float(self.timeout_multiplier), 6),
            "generation_run_id": int(self.generation_run_id),
            "source_ingestion_run_id": int(self.source_ingestion_run_id),
            "incident_seed": self.incident_seed,
            "incident_model_version": INCIDENT_MODEL_VERSION,
            "incident_config_version": INCIDENT_CONFIG_VERSION,
        }

    @property
    def incident_key(self) -> str:
        """Deterministic SHA-256 identity of this definition."""
        payload = json.dumps(self.identity_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_iso(value: datetime) -> str:
    from datetime import timezone

    return value.astimezone(timezone.utc).isoformat()


def find_incident_by_key(session: Session, incident_key: str) -> Incident | None:
    return session.scalar(select(Incident).where(Incident.incident_key == incident_key))


def create_incident(session: Session, definition: IncidentDefinition) -> tuple[Incident, bool]:
    """
    Persist an incident idempotently.

    Returns (incident, created). Re-injecting an identical definition returns the
    existing row with created=False rather than producing a duplicate -- required so a
    pipeline can be re-run safely without silently multiplying incidents.
    """
    definition.validate()
    key = definition.incident_key

    existing = find_incident_by_key(session, key)
    if existing is not None:
        return existing, False

    incident = Incident(
        incident_key=key,
        incident_name=definition.incident_name,
        incident_type=definition.incident_type,
        affected_gateway_id=definition.affected_gateway_id,
        affected_segment=definition.affected_segment,
        incident_start=definition.incident_start,
        incident_end=definition.incident_end,
        failure_multiplier=definition.failure_multiplier,
        latency_multiplier=definition.latency_multiplier,
        timeout_multiplier=definition.timeout_multiplier,
        target_failure_rate=definition.target_failure_rate,
        generation_run_id=definition.generation_run_id,
        source_ingestion_run_id=definition.source_ingestion_run_id,
        status="CREATED",
        incident_seed=definition.incident_seed,
        incident_model_version=INCIDENT_MODEL_VERSION,
        incident_config_version=INCIDENT_CONFIG_VERSION,
        notes=definition.notes,
    )
    session.add(incident)
    session.flush()

    # Ground truth is written once, into its isolated evaluation-only table. Nothing in
    # the diagnosis path reads it back.
    if definition.ground_truth_root_cause:
        session.add(
            IncidentGroundTruth(
                incident_id=incident.incident_id,
                ground_truth_root_cause=definition.ground_truth_root_cause,
                ground_truth_gateway_id=definition.affected_gateway_id,
                ground_truth_detail=definition.ground_truth_detail,
            )
        )
        session.flush()

    return incident, True


def advance_status(session: Session, incident: Incident, new_status: str) -> None:
    """
    Move an incident forward through its lifecycle.

    Forward-only: re-applying the current state is a no-op, but moving backwards is an
    error. An incident that has been DIAGNOSED cannot quietly revert to CREATED and
    lose the fact that a conclusion was already published against it.
    """
    if new_status not in INCIDENT_STATUS_ORDER:
        raise IncidentLifecycleError(f"unknown incident status {new_status!r}")

    current_rank = INCIDENT_STATUS_ORDER[incident.status]
    new_rank = INCIDENT_STATUS_ORDER[new_status]
    if new_rank < current_rank:
        raise IncidentLifecycleError(
            f"cannot move incident {incident.incident_id} backwards: "
            f"{incident.status} -> {new_status}"
        )
    if new_rank == current_rank:
        return

    incident.status = new_status
    session.flush()


def ensure_status(session: Session, incident: Incident, status: str) -> None:
    """
    Advance to `status` only if that is forward progress; otherwise leave it alone.

    Re-running a pipeline over an already-diagnosed incident is legitimate (it is how
    reproducibility is verified), and it should not fail merely because the lifecycle
    has already moved past the stage being re-executed. Explicit transitions still use
    `advance_status`, which rejects going backwards.
    """
    if status not in INCIDENT_STATUS_ORDER:
        raise IncidentLifecycleError(f"unknown incident status {status!r}")
    if INCIDENT_STATUS_ORDER[status] > INCIDENT_STATUS_ORDER[incident.status]:
        incident.status = status
        session.flush()


def load_ground_truth(session: Session, incident_id: int) -> IncidentGroundTruth | None:
    """
    EVALUATION ONLY -- never call this from a detection, evidence, hypothesis, or RCA
    code path. It exists so a test or an evaluation report can score a diagnosis that
    has ALREADY been produced without it.
    """
    return session.get(IncidentGroundTruth, incident_id)
