"""
Aventum canonical transaction ingestion (Day 2A).

Pipeline:
    raw source -> integrity check -> schema drift check -> read-only extraction
    -> deterministic normalization -> validation -> quarantine invalid
    -> staging load -> staging verification -> atomic promotion
    -> post-load verification -> ingestion audit record

Scope boundary: this package ends at a validated, reproducible, auditable canonical
`transactions` table. It contains no synthetic infrastructure, incident, anomaly,
RCA, simulation, or agent logic.
"""

__version__ = "0.1.0"
