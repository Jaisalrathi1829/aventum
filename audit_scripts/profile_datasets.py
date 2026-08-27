"""
Day 1 audit: computational profiler for every raw dataset.
Read-only. Never writes to data/raw/. Outputs JSON profiles to audit_scripts/output/.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
OUT = Path(__file__).resolve().parent / "output"
OUT.mkdir(exist_ok=True)

pd.set_option("display.max_columns", None)


def jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if obj is pd.NaT:
        return None
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    return obj


def profile_series(s: pd.Series, name: str) -> dict:
    n = len(s)
    nulls = int(s.isna().sum())
    non_null = s.dropna()
    uniq = int(non_null.nunique())
    prof = {
        "column": name,
        "pandas_dtype": str(s.dtype),
        "null_count": nulls,
        "null_pct": round(100 * nulls / n, 4) if n else None,
        "unique_count": uniq,
        "unique_pct_of_nonnull": round(100 * uniq / len(non_null), 4) if len(non_null) else None,
    }

    # try numeric coercion to check "looks numeric but stored as object"
    numeric_try = pd.to_numeric(non_null, errors="coerce")
    numeric_coverage = float(numeric_try.notna().mean()) if len(non_null) else 0.0

    if pd.api.types.is_numeric_dtype(s):
        desc = non_null.describe(percentiles=[0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
        prof["numeric_summary"] = {k: jsonable(v) for k, v in desc.to_dict().items()}
        prof["min"] = jsonable(non_null.min()) if len(non_null) else None
        prof["max"] = jsonable(non_null.max()) if len(non_null) else None
        prof["negative_count"] = int((non_null < 0).sum())
        prof["zero_count"] = int((non_null == 0).sum())
    elif numeric_coverage > 0.9:
        prof["looks_numeric_but_stored_as_text"] = True
        prof["numeric_coverage_pct"] = round(100 * numeric_coverage, 2)
        prof["non_numeric_sample_values"] = jsonable(
            non_null[numeric_try.isna()].astype(str).unique()[:10].tolist()
        )
    else:
        vc = non_null.astype(str).value_counts()
        prof["top_values"] = {str(k): int(v) for k, v in vc.head(15).items()}
        prof["rare_values_count_1"] = int((vc == 1).sum())
        # whitespace / casing inconsistency check
        stripped = non_null.astype(str).str.strip()
        prof["has_leading_trailing_whitespace"] = bool((non_null.astype(str) != stripped).any())
        lower = stripped.str.lower()
        distinct_raw = stripped.nunique()
        distinct_lower = lower.nunique()
        prof["casing_collapses_categories"] = bool(distinct_lower < distinct_raw)
        prof["distinct_raw_vs_lowercased"] = [int(distinct_raw), int(distinct_lower)]

    placeholder_tokens = {"na", "n/a", "null", "none", "unknown", "-", "--", "nan", ""}
    as_str_lower = non_null.astype(str).str.strip().str.lower()
    placeholder_hits = as_str_lower.isin(placeholder_tokens).sum()
    prof["placeholder_like_value_count"] = int(placeholder_hits)

    return prof


def try_parse_dates(s: pd.Series):
    try:
        parsed = pd.to_datetime(s, errors="coerce", format="mixed")
    except Exception:
        parsed = pd.to_datetime(s, errors="coerce")
    coverage = parsed.notna().mean() if len(s) else 0
    return parsed, coverage


def profile_dataframe(df: pd.DataFrame, dataset_name: str) -> dict:
    n_rows, n_cols = df.shape
    dup_rows = int(df.duplicated().sum())

    columns_profile = []
    date_candidates = {}
    for col in df.columns:
        colp = profile_series(df[col], col)
        columns_profile.append(colp)
        if df[col].dtype == object or "date" in col.lower() or "time" in col.lower():
            parsed, cov = try_parse_dates(df[col])
            if cov > 0.8:
                date_candidates[col] = {
                    "coverage_pct": round(100 * cov, 2),
                    "min": jsonable(parsed.min()),
                    "max": jsonable(parsed.max()),
                }

    # candidate primary keys: high uniqueness object/int columns
    pk_candidates = []
    for colp in columns_profile:
        pct = colp.get("unique_pct_of_nonnull")
        if pct is not None and pct >= 99.0 and colp["null_count"] == 0:
            pk_candidates.append({"column": colp["column"], "uniqueness_pct": pct})

    return {
        "dataset": dataset_name,
        "row_count": n_rows,
        "column_count": n_cols,
        "duplicate_row_count": dup_rows,
        "duplicate_row_pct": round(100 * dup_rows / n_rows, 4) if n_rows else None,
        "columns": [c for c in df.columns],
        "date_like_columns": date_candidates,
        "candidate_primary_keys": pk_candidates,
        "column_profiles": columns_profile,
    }


def load_and_profile(path: Path, dataset_name: str, **read_kwargs):
    print(f"--- Profiling {dataset_name} ({path.name}) ---", file=sys.stderr)
    try:
        df = pd.read_csv(path, encoding="utf-8", **read_kwargs)
        detected_encoding = "utf-8"
    except UnicodeDecodeError:
        df = pd.read_csv(path, encoding="cp1252", **read_kwargs)
        detected_encoding = "cp1252 (utf-8 decode failed)"
    prof = profile_dataframe(df, dataset_name)
    prof["source_file"] = str(path.relative_to(RAW.parent.parent))
    prof["file_size_bytes"] = path.stat().st_size
    prof["detected_encoding"] = detected_encoding
    out_path = OUT / f"{dataset_name}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(jsonable(prof), f, indent=2)
    print(f"    rows={prof['row_count']} cols={prof['column_count']} -> {out_path}", file=sys.stderr)
    return df, prof


def main():
    datasets = {}

    # --- Main candidate transaction-level / time-series datasets ---
    df, _ = load_and_profile(
        RAW / "Indian UPI Transactions" / "upi_india_monthly_enriched.csv",
        "upi_india_monthly_enriched",
    )
    datasets["upi_india_monthly_enriched"] = df

    df, _ = load_and_profile(
        RAW / "UPI Transaction Insights Dataset" / "upi_transaction_insights_dataset.csv",
        "upi_transaction_insights_dataset",
    )
    datasets["upi_transaction_insights_dataset"] = df

    df, _ = load_and_profile(
        RAW / "UPI Transactions 2024 Dataset" / "upi_transactions_2024.csv",
        "upi_transactions_2024",
    )
    datasets["upi_transactions_2024"] = df

    # --- NPCI ecosystem statistics (monthly national aggregates) ---
    npci_dir = RAW / "NPCI Products Statistics Since Launch"

    simple_monthly = {
        "npci_bhim_statistics": "BHIM product Statistics.csv",
        "npci_fastag_statistics": "Fastag Statistics.csv",
        "npci_imps_statistics": "IMPS Statistics.csv",
        "npci_star99_statistics": "PS99.xlsx.csv",
        "npci_upi_product_statistics": "UPI Product  Statistics.csv",
    }
    for name, fname in simple_monthly.items():
        df, _ = load_and_profile(npci_dir / fname, name)
        datasets[name] = df

    # UPI mandate creation - check header shape first (small file)
    df, _ = load_and_profile(npci_dir / "UPI mandate creation.csv", "npci_upi_mandate_creation")
    datasets["npci_upi_mandate_creation"] = df

    # Bank/PSP performance snapshot files (single point-in-time, not monthly series)
    df, _ = load_and_profile(npci_dir / "UPI Beneficiary Bank.csv", "npci_upi_beneficiary_bank")
    datasets["npci_upi_beneficiary_bank"] = df

    df, _ = load_and_profile(npci_dir / "UPI Payers Perforance PSP.csv", "npci_upi_payers_performance_psp")
    datasets["npci_upi_payers_performance_psp"] = df

    df, _ = load_and_profile(npci_dir / "UPI Remitter Banks.csv", "npci_upi_remitter_banks")
    datasets["npci_upi_remitter_banks"] = df

    # Year wise digital transaction (yearly, multi-instrument)
    df, _ = load_and_profile(npci_dir / "Year wise Digital Transaction.csv", "npci_year_wise_digital_transaction")
    datasets["npci_year_wise_digital_transaction"] = df

    # UPI Apps.csv has a 3-row multi-index header -> profile raw + attempt structured parse separately
    print("--- Profiling npci_upi_apps (multi-header, raw) ---", file=sys.stderr)
    raw_apps = pd.read_csv(npci_dir / "UPI Apps.csv", header=None)
    apps_profile = {
        "dataset": "npci_upi_apps_RAW",
        "note": "3-row multi-level header (title row, group row, sub-metric row). See DATASET_INVENTORY.md for structured interpretation.",
        "row_count": int(raw_apps.shape[0]),
        "column_count": int(raw_apps.shape[1]),
        "first_6_rows": jsonable(raw_apps.head(6).values.tolist()),
        "source_file": str((npci_dir / "UPI Apps.csv").relative_to(RAW.parent.parent)),
        "file_size_bytes": (npci_dir / "UPI Apps.csv").stat().st_size,
    }
    with open(OUT / "npci_upi_apps_RAW.json", "w", encoding="utf-8") as f:
        json.dump(jsonable(apps_profile), f, indent=2)
    print(f"    rows={apps_profile['row_count']} cols={apps_profile['column_count']}", file=sys.stderr)

    print("\nAll profiles written to", OUT, file=sys.stderr)


if __name__ == "__main__":
    main()
