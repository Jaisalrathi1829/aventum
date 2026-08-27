"""
Set-based cohort metric computation.

Every metric Day 3 reasons about is produced here, by SQL, over whole cohorts at once.
Nothing in this module runs a query inside a loop: one statement per (window, dimension
set), aggregated in the database. That is what keeps detection over six dimensions plus
three intersections to a handful of round trips instead of thousands.

THE EFFECTIVE-OUTCOME SURFACE
-----------------------------
Detection must read one consistent outcome per transaction, whether or not an incident
touches it. Rather than materialising 250,000 unchanged "simulated" rows, the effective
outcome is resolved by an outer join:

    effective_status = COALESCE(simulated_incident_outcomes.simulated_status,
                                transactions.status)

A row inside an incident window resolves to its modelled outcome; every other row
resolves to observed history. `outcome_source` travels with it so an evidence record can
always state which epistemic layer its number came from -- OBSERVED or SIMULATED are
never silently merged into an unlabelled "status".

Passing incident_id = NULL yields a pure-observed surface, which is exactly what the
no-incident false-positive scenario needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from .constants import DIMENSION_SQL, INFRASTRUCTURE_SIDE_RESPONSES

# Rendered once: the response codes that indicate infrastructure rather than issuer.
_INFRA_SQL_LIST = ", ".join(f"'{code}'" for code in sorted(INFRASTRUCTURE_SIDE_RESPONSES))

_EFFECTIVE_CTE = f"""
WITH effective AS (
    SELECT
        t.transaction_id,
        t.amount,
        t.timestamp,
        t.sender_bank,
        t.payment_method,
        t.region,
        t.device,
        t.network,
        a.selected_gateway_id                                     AS gateway_id,
        COALESCE(s.simulated_status, t.status)                    AS effective_status,
        COALESCE(s.simulated_latency_ms, a.gateway_latency_ms)    AS effective_latency_ms,
        COALESCE(s.simulated_response_code, a.gateway_response_code)
                                                                  AS effective_response_code,
        CASE WHEN s.transaction_id IS NULL THEN 'OBSERVED' ELSE 'SIMULATED' END
                                                                  AS outcome_source
    FROM transactions t
    JOIN synthetic_infrastructure_assignments a
      ON a.transaction_id = t.transaction_id
     AND a.generation_run_id = :generation_run_id
    LEFT JOIN simulated_incident_outcomes s
      ON s.transaction_id = t.transaction_id
     AND s.incident_id = :incident_id
    WHERE t.timestamp >= :window_start
      AND t.timestamp <  :window_end
)
"""

# The metric block, shared by every grouping. `amount` is the authoritative observed
# transaction value from `transactions`, so GMV figures are never invented.
_METRIC_SELECT = f"""
        count(*)                                              AS volume,
        count(*) FILTER (WHERE effective_status = 'FAILED')    AS failures,
        COALESCE(sum(amount), 0)                              AS gmv_total,
        COALESCE(sum(amount) FILTER (WHERE effective_status = 'FAILED'), 0)
                                                              AS gmv_at_risk,
        COALESCE(percentile_cont(0.50) WITHIN GROUP (ORDER BY effective_latency_ms), 0)
                                                              AS latency_p50,
        COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY effective_latency_ms), 0)
                                                              AS latency_p95,
        COALESCE(percentile_cont(0.99) WITHIN GROUP (ORDER BY effective_latency_ms), 0)
                                                              AS latency_p99,
        count(*) FILTER (WHERE effective_response_code = 'TIMEOUT')
                                                              AS timeouts,
        count(*) FILTER (WHERE effective_response_code IN ({_INFRA_SQL_LIST}))
                                                              AS infrastructure_side,
        count(*) FILTER (WHERE outcome_source = 'SIMULATED')   AS simulated_rows
"""


@dataclass(frozen=True)
class CohortMetrics:
    """Metrics for one cohort over one window. Pure data; no interpretation."""

    cohort_key: str
    cohort_definition: dict
    dimensions: tuple[str, ...]
    volume: int
    failures: int
    gmv_total: float
    gmv_at_risk: float
    latency_p50: float
    latency_p95: float
    latency_p99: float
    timeouts: int
    infrastructure_side: int
    simulated_rows: int

    @property
    def failure_rate(self) -> float:
        return (self.failures / self.volume) if self.volume else 0.0

    @property
    def timeout_rate(self) -> float:
        return (self.timeouts / self.volume) if self.volume else 0.0

    @property
    def infrastructure_side_rate(self) -> float:
        return (self.infrastructure_side / self.volume) if self.volume else 0.0

    @property
    def source_layer(self) -> str:
        """
        Which epistemic layer dominated this cohort's numbers.

        A cohort containing any modelled row is reported as SIMULATED: claiming
        OBSERVED for a partly-modelled population would be exactly the flattening the
        truth model forbids.
        """
        return "SIMULATED" if self.simulated_rows else "OBSERVED"

    def as_dict(self) -> dict:
        return {
            "volume": self.volume,
            "failures": self.failures,
            "failure_rate": round(self.failure_rate, 6),
            "gmv_total": round(self.gmv_total, 2),
            "gmv_at_risk": round(self.gmv_at_risk, 2),
            "latency_p50": round(self.latency_p50, 2),
            "latency_p95": round(self.latency_p95, 2),
            "latency_p99": round(self.latency_p99, 2),
            "timeout_rate": round(self.timeout_rate, 6),
            "infrastructure_side_rate": round(self.infrastructure_side_rate, 6),
            "source_layer": self.source_layer,
        }


def cohort_key_for(definition: dict) -> str:
    """Stable, readable, sortable cohort identity, e.g. `gateway=gateway_C|sender_bank=SBI`."""
    return "|".join(f"{key}={definition[key]}" for key in sorted(definition))


def _row_to_metrics(row: dict, dimensions: tuple[str, ...]) -> CohortMetrics:
    definition = {dimension: row[DIMENSION_SQL[dimension]] for dimension in dimensions}
    return CohortMetrics(
        cohort_key=cohort_key_for(definition) if definition else "ALL",
        cohort_definition=definition,
        dimensions=dimensions,
        volume=int(row["volume"]),
        failures=int(row["failures"]),
        gmv_total=float(row["gmv_total"]),
        gmv_at_risk=float(row["gmv_at_risk"]),
        latency_p50=float(row["latency_p50"]),
        latency_p95=float(row["latency_p95"]),
        latency_p99=float(row["latency_p99"]),
        timeouts=int(row["timeouts"]),
        infrastructure_side=int(row["infrastructure_side"]),
        simulated_rows=int(row["simulated_rows"]),
    )


def cohort_metrics(
    session: Session,
    generation_run_id: int,
    window_start: datetime,
    window_end: datetime,
    dimensions: tuple[str, ...],
    incident_id: int | None = None,
) -> list[CohortMetrics]:
    """
    Aggregate metrics for every cohort along `dimensions` in one statement.

    `dimensions` may be empty, which yields a single whole-population row -- used as the
    systemic-hypothesis comparison point.
    """
    for dimension in dimensions:
        if dimension not in DIMENSION_SQL:
            raise ValueError(f"unknown cohort dimension {dimension!r}")

    columns = [DIMENSION_SQL[dimension] for dimension in dimensions]
    if columns:
        select_columns = ", ".join(columns) + ","
        group_by = "GROUP BY " + ", ".join(columns)
        order_by = "ORDER BY " + ", ".join(columns)
    else:
        select_columns = ""
        group_by = ""
        order_by = ""

    sql = text(
        f"""
        {_EFFECTIVE_CTE}
        SELECT
            {select_columns}
            {_METRIC_SELECT}
        FROM effective
        {group_by}
        {order_by}
        """
    )

    rows = (
        session.execute(
            sql,
            {
                "generation_run_id": generation_run_id,
                "incident_id": incident_id,
                "window_start": window_start,
                "window_end": window_end,
            },
        )
        .mappings()
        .all()
    )
    return [_row_to_metrics(dict(row), dimensions) for row in rows]


def residual_metrics(
    session: Session,
    generation_run_id: int,
    window_start: datetime,
    window_end: datetime,
    dimensions: tuple[str, ...],
    exclude: dict[str, str],
    incident_id: int | None = None,
) -> list[CohortMetrics]:
    """
    Cohort metrics with a confounding population removed.

    Used to answer the question that separates a cause from its shadow: if `network=5G`
    looks anomalous only because a degraded gateway happens to carry a lot of 5G
    traffic, then excluding that gateway should return 5G to its baseline. If instead
    the cohort is still anomalous with the suspect removed, it is moving on its own.

    `exclude` is applied as a NOT-match, so the result is the same cohort computed over
    the complement of the confounder.
    """
    for dimension in dimensions:
        if dimension not in DIMENSION_SQL:
            raise ValueError(f"unknown cohort dimension {dimension!r}")
    for dimension in exclude:
        if dimension not in DIMENSION_SQL:
            raise ValueError(f"unknown exclusion dimension {dimension!r}")

    columns = [DIMENSION_SQL[dimension] for dimension in dimensions]
    select_columns = (", ".join(columns) + ",") if columns else ""
    group_by = ("GROUP BY " + ", ".join(columns)) if columns else ""
    order_by = ("ORDER BY " + ", ".join(columns)) if columns else ""

    params: dict = {
        "generation_run_id": generation_run_id,
        "incident_id": incident_id,
        "window_start": window_start,
        "window_end": window_end,
    }
    clauses = []
    for index, (dimension, value) in enumerate(sorted(exclude.items())):
        placeholder = f"exclude_{index}"
        clauses.append(f"{DIMENSION_SQL[dimension]} <> :{placeholder}")
        params[placeholder] = value
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = text(
        f"""
        {_EFFECTIVE_CTE}
        SELECT
            {select_columns}
            {_METRIC_SELECT}
        FROM effective
        {where}
        {group_by}
        {order_by}
        """
    )
    rows = session.execute(sql, params).mappings().all()
    return [_row_to_metrics(dict(row), dimensions) for row in rows]


def population_metrics(
    session: Session,
    generation_run_id: int,
    window_start: datetime,
    window_end: datetime,
    incident_id: int | None = None,
) -> CohortMetrics:
    """Whole-window metrics with no grouping."""
    results = cohort_metrics(
        session,
        generation_run_id=generation_run_id,
        window_start=window_start,
        window_end=window_end,
        dimensions=(),
        incident_id=incident_id,
    )
    if results:
        return results[0]
    return CohortMetrics(
        cohort_key="ALL",
        cohort_definition={},
        dimensions=(),
        volume=0,
        failures=0,
        gmv_total=0.0,
        gmv_at_risk=0.0,
        latency_p50=0.0,
        latency_p95=0.0,
        latency_p99=0.0,
        timeouts=0,
        infrastructure_side=0,
        simulated_rows=0,
    )


def index_by_key(metrics: list[CohortMetrics]) -> dict[str, CohortMetrics]:
    return {metric.cohort_key: metric for metric in metrics}


class MetricStore:
    """
    Memoised cohort metrics for one analysis.

    Detection scans nine dimension sets across two windows; the evidence engine then
    needs the same aggregates again to describe control groups and blast radius. Without
    sharing, that is roughly double the queries for identical numbers -- and worse, two
    code paths could drift and report slightly different values for the same metric.

    Keyed on (dimensions, window), so a repeat request is free and every consumer is
    guaranteed to be quoting the same aggregate.
    """

    def __init__(self, session: Session, generation_run_id: int, incident_id: int | None) -> None:
        self._session = session
        self._generation_run_id = generation_run_id
        self._incident_id = incident_id
        self._cache: dict[tuple, list[CohortMetrics]] = {}
        self.query_count = 0

    def metrics(
        self,
        dimensions: tuple[str, ...],
        window: tuple[datetime, datetime],
    ) -> list[CohortMetrics]:
        key = (dimensions, window[0], window[1])
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        result = cohort_metrics(
            self._session,
            generation_run_id=self._generation_run_id,
            window_start=window[0],
            window_end=window[1],
            dimensions=dimensions,
            incident_id=self._incident_id,
        )
        self._cache[key] = result
        self.query_count += 1
        return result

    def indexed(
        self,
        dimensions: tuple[str, ...],
        window: tuple[datetime, datetime],
    ) -> dict[str, CohortMetrics]:
        return index_by_key(self.metrics(dimensions, window))

    def residual(
        self,
        dimensions: tuple[str, ...],
        window: tuple[datetime, datetime],
        exclude: dict[str, str],
    ) -> dict[str, CohortMetrics]:
        """Memoised residual metrics with a confounding population removed."""
        key = (dimensions, window[0], window[1], tuple(sorted(exclude.items())))
        cached = self._cache.get(key)
        if cached is None:
            cached = residual_metrics(
                self._session,
                generation_run_id=self._generation_run_id,
                window_start=window[0],
                window_end=window[1],
                dimensions=dimensions,
                exclude=exclude,
                incident_id=self._incident_id,
            )
            self._cache[key] = cached
            self.query_count += 1
        return index_by_key(cached)

    def population(self, window: tuple[datetime, datetime]) -> CohortMetrics:
        rows = self.metrics((), window)
        if rows:
            return rows[0]
        return CohortMetrics(
            cohort_key="ALL",
            cohort_definition={},
            dimensions=(),
            volume=0,
            failures=0,
            gmv_total=0.0,
            gmv_at_risk=0.0,
            latency_p50=0.0,
            latency_p95=0.0,
            latency_p99=0.0,
            timeouts=0,
            infrastructure_side=0,
            simulated_rows=0,
        )
