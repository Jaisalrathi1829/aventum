"""
Source schema drift protection (Day 2A §3).

Runs BEFORE any canonical mutation. If a required column vanished or a critical type
changed, the ingestion fails here -- it never silently adapts to a source whose shape
no longer matches what Day 1 audited.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .constants import (
    EXPECTED_SOURCE_COLUMNS,
    REQUIRED_SOURCE_COLUMNS,
    UNMAPPED_SOURCE_COLUMNS,
)


class SchemaDriftError(RuntimeError):
    """Raised when source schema drift is severe enough to abort ingestion."""


@dataclass
class SchemaDriftReport:
    """Full comparison of the observed source header against the audited expectation."""

    observed_columns: list[str]
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    duplicated: list[str] = field(default_factory=list)
    type_changes: list[str] = field(default_factory=list)

    @property
    def is_fatal(self) -> bool:
        """Fatal iff the mapping cannot be executed correctly."""
        return bool(self.missing_required or self.duplicated or self.type_changes)

    @property
    def has_any_drift(self) -> bool:
        return bool(
            self.missing_required
            or self.missing_optional
            or self.unexpected
            or self.duplicated
            or self.type_changes
        )

    def summary(self) -> str:
        if not self.has_any_drift:
            return "No schema drift detected; source matches the Day 1 audited schema exactly."
        parts: list[str] = []
        if self.missing_required:
            parts.append(f"MISSING REQUIRED columns: {sorted(self.missing_required)}")
        if self.duplicated:
            parts.append(f"DUPLICATE column names: {sorted(self.duplicated)}")
        if self.type_changes:
            parts.append(f"CRITICAL TYPE CHANGES: {self.type_changes}")
        if self.missing_optional:
            parts.append(
                f"missing expected-but-unmapped columns (non-fatal): {sorted(self.missing_optional)}"
            )
        if self.unexpected:
            parts.append(f"unexpected new columns (non-fatal): {sorted(self.unexpected)}")
        return " | ".join(parts)

    def to_dict(self) -> dict:
        return {
            "observed_columns": self.observed_columns,
            "missing_required": sorted(self.missing_required),
            "missing_optional": sorted(self.missing_optional),
            "unexpected": sorted(self.unexpected),
            "duplicated": sorted(self.duplicated),
            "type_changes": self.type_changes,
            "is_fatal": self.is_fatal,
            "summary": self.summary(),
        }


def read_source_header(path: Path) -> list[str]:
    """
    Read only the header row, preserving duplicates.

    pandas silently de-duplicates repeated headers (`x`, `x.1`), which would hide a
    genuine drift signal, so the raw csv module is used for this check instead.
    """
    with open(path, "r", encoding="utf-8", newline="") as handle:
        try:
            header = next(csv.reader(handle))
        except StopIteration:
            raise SchemaDriftError(f"Source file has no header row: {path}") from None
    return [column.strip() for column in header]


def _find_duplicates(columns: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for column in columns:
        if column in seen:
            duplicates.add(column)
        seen.add(column)
    return sorted(duplicates)


# A column counts as having CHANGED TYPE only when most of its sampled values stop
# conforming. A handful of bad values is a per-record data-quality problem, which §9
# requires be quarantined with an inspectable reason -- aborting the entire ingestion
# because one row of 250,000 is malformed would be the wrong trade. Above this
# threshold the column's format has genuinely changed and the documented mapping can
# no longer be executed correctly, which is what §3 requires us to fail on.
_TYPE_DRIFT_FAILURE_THRESHOLD = 0.5


def _check_critical_types(sample: pd.DataFrame) -> list[str]:
    """
    Verify that critical source columns still hold the types the mapping assumes.

    Only two source columns carry a hard type contract:
      - `amount (INR)` must be numeric-coercible
      - `timestamp`    must be datetime-parseable
    Everything else is free text whose vocabulary is checked per record by validation.
    """
    problems: list[str] = []

    def _assess(column: str, parsed: pd.Series) -> None:
        total = len(parsed)
        if total == 0:
            return
        bad_mask = parsed.isna()
        bad_ratio = float(bad_mask.mean())
        if bad_ratio > _TYPE_DRIFT_FAILURE_THRESHOLD:
            examples = sample.loc[bad_mask, column].astype(str).unique()[:5]
            problems.append(
                f"{column!r} no longer conforms to its expected type: "
                f"{bad_ratio:.1%} of {total} sampled values failed to parse "
                f"(threshold {_TYPE_DRIFT_FAILURE_THRESHOLD:.0%}); examples: {list(examples)}"
            )

    if "amount (INR)" in sample.columns:
        _assess("amount (INR)", pd.to_numeric(sample["amount (INR)"], errors="coerce"))

    if "timestamp" in sample.columns:
        _assess(
            "timestamp",
            pd.to_datetime(sample["timestamp"], errors="coerce", format="ISO8601"),
        )

    return problems


def detect_schema_drift(path: Path, sample_rows: int = 1000) -> SchemaDriftReport:
    """
    Compare the observed source schema against the Day 1 audited expectation.

    Reads only the header plus a bounded sample -- this check must be cheap enough to
    always run before the full extraction.
    """
    observed = read_source_header(path)
    observed_set = set(observed)
    expected_set = set(EXPECTED_SOURCE_COLUMNS)

    report = SchemaDriftReport(observed_columns=observed)
    report.duplicated = _find_duplicates(observed)
    report.missing_required = sorted(REQUIRED_SOURCE_COLUMNS - observed_set)
    report.missing_optional = sorted((expected_set - REQUIRED_SOURCE_COLUMNS) - observed_set)
    report.unexpected = sorted(observed_set - expected_set)

    # Type checks need actual values; skip if a required column is already missing
    # (the fatal error is clearer without a cascade of type complaints).
    if not report.missing_required and not report.duplicated:
        sample = pd.read_csv(path, nrows=sample_rows, encoding="utf-8")
        sample.columns = [str(c).strip() for c in sample.columns]
        report.type_changes = _check_critical_types(sample)

    return report


def assert_schema_compatible(path: Path, sample_rows: int = 1000) -> SchemaDriftReport:
    """
    Fail loudly on fatal drift; return the report otherwise.

    A renamed column surfaces as (missing_required + unexpected) rather than as a
    dedicated "renamed" category -- deliberately, since guessing that `txn_id` is a
    rename of `transaction id` would be exactly the silent adaptation §3 forbids.
    """
    report = detect_schema_drift(path, sample_rows=sample_rows)
    if report.is_fatal:
        raise SchemaDriftError(
            "Source schema drift detected -- ingestion aborted BEFORE any canonical "
            f"mutation.\n{report.summary()}\n"
            f"Observed columns: {report.observed_columns}\n"
            f"Expected columns: {list(EXPECTED_SOURCE_COLUMNS)}\n"
            "If this drift is intentional, update docs/AVENTUM_CANONICAL_SCHEMA.md and "
            "aventum_ingest/constants.py deliberately, then bump SCHEMA_VERSION."
        )
    return report


def unmapped_columns_present(observed_columns: list[str]) -> list[str]:
    """Expected-but-deliberately-unmapped columns present in this file (informational)."""
    return sorted(UNMAPPED_SOURCE_COLUMNS & set(observed_columns))
