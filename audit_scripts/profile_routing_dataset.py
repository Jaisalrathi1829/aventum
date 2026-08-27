"""
Full computational profile of the Nigerian Card Payment Dataset for Predictive
Routing (parquet). Read-only. Outputs to audit_scripts/output/.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
OUT = Path(__file__).resolve().parent / "output"
FILE = RAW / "Nigerian Card Payment Dataset for Predictive Routing.parquet"

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 160)

df = pd.read_parquet(FILE)
print(f"Loaded {len(df):,} rows, {len(df.columns)} columns")
print(f"Columns: {list(df.columns)}")
print(f"Dtypes:\n{df.dtypes}")

print()
print("=" * 80)
print("A. NULLS / DUPLICATES / BASIC SHAPE")
print("=" * 80)
print(f"Total rows: {len(df):,}")
print(f"Fully duplicate rows: {df.duplicated().sum():,}")
print("Null counts per column:")
print(df.isna().sum())

print()
print("=" * 80)
print("B. GRAIN: transaction_id repetition")
print("=" * 80)
tx_counts = df["transaction_id"].value_counts()
print(f"Distinct transaction_id: {df['transaction_id'].nunique():,} (rows: {len(df):,})")
print(f"Rows per transaction_id: min={tx_counts.min()}, median={tx_counts.median()}, "
      f"mean={tx_counts.mean():.3f}, max={tx_counts.max()}")
print("Distribution of rows-per-transaction_id:")
print(tx_counts.value_counts().sort_index())

print()
print("=" * 80)
print("C. GRAIN: reference field uniqueness")
print("=" * 80)
print(f"Distinct reference: {df['reference'].nunique():,} (rows: {len(df):,})")
print(f"reference 100% unique? {df['reference'].nunique() == len(df)}")

print()
print("=" * 80)
print("D. For a repeated transaction_id, what differs across its rows?")
print("=" * 80)
dupe_ids = tx_counts[tx_counts > 1].index[:5]
for tid in dupe_ids:
    sub = df[df["transaction_id"] == tid].sort_values("timestamp")
    print(f"\n--- transaction_id={tid} ({len(sub)} rows) ---")
    print(sub[["timestamp", "reference", "rail_id", "payment_channel", "status",
               "gateway_response", "error_code", "latency_ms", "amount"]].to_string(index=False))

print()
print("=" * 80)
print("E. CATEGORICAL DISTRIBUTIONS")
print("=" * 80)
for col in ["merchant_segment", "rail_id", "payment_channel", "status", "gateway_response",
            "error_code", "region", "country"]:
    print(f"\n--- {col} (nunique={df[col].nunique()}) ---")
    print(df[col].value_counts(dropna=False).to_string())

print()
print("=" * 80)
print("F. NUMERIC DISTRIBUTIONS")
print("=" * 80)
print("amount:")
print(df["amount"].describe(percentiles=[.01, .05, .25, .5, .75, .9, .95, .99]))
print(f"\namount negative count: {(df['amount'] < 0).sum()}, zero count: {(df['amount'] == 0).sum()}")

print("\nlatency_ms:")
print(df["latency_ms"].describe(percentiles=[.01, .05, .25, .5, .75, .9, .95, .99]))
print(f"\nlatency_ms negative count: {(df['latency_ms'] < 0).sum()}, zero count: {(df['latency_ms'] == 0).sum()}")
print(f"latency_ms null count: {df['latency_ms'].isna().sum()}")

print()
print("=" * 80)
print("G. TEMPORAL RANGE / DENSITY")
print("=" * 80)
print(f"timestamp min: {df['timestamp'].min()}, max: {df['timestamp'].max()}")
span = (df["timestamp"].max() - df["timestamp"].min())
print(f"Span: {span}")
per_day = df.set_index("timestamp").resample("D").size()
print("Rows per day:")
print(per_day)
per_min = df.set_index("timestamp").resample("min").size()
print(f"\nRows per minute: min={per_min.min()}, median={per_min.median()}, mean={per_min.mean():.2f}, max={per_min.max()}")

print()
print("=" * 80)
print("H. merchant_id cardinality")
print("=" * 80)
print(f"Distinct merchant_id: {df['merchant_id'].nunique()}")
print(df["merchant_id"].value_counts().describe())

OUT.mkdir(exist_ok=True)
with open(OUT / "routing_dataset_shape.json", "w", encoding="utf-8") as f:
    json.dump({
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "distinct_transaction_id": int(df["transaction_id"].nunique()),
        "distinct_reference": int(df["reference"].nunique()),
        "distinct_merchant_id": int(df["merchant_id"].nunique()),
        "distinct_rail_id": int(df["rail_id"].nunique()),
        "rail_id_values": sorted(df["rail_id"].dropna().unique().tolist()),
        "distinct_payment_channel": int(df["payment_channel"].nunique()),
        "payment_channel_values": sorted(df["payment_channel"].dropna().unique().tolist()),
        "distinct_status": sorted(df["status"].dropna().unique().tolist()),
        "distinct_gateway_response": sorted(df["gateway_response"].dropna().unique().tolist()),
        "distinct_error_code": sorted(df["error_code"].dropna().unique().tolist()),
        "distinct_region": sorted(df["region"].dropna().unique().tolist()),
        "distinct_country": sorted(df["country"].dropna().unique().tolist()),
        "timestamp_min": str(df["timestamp"].min()),
        "timestamp_max": str(df["timestamp"].max()),
    }, f, indent=2)
print("\nShape summary written to output/routing_dataset_shape.json")
