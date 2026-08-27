"""
Deep analysis of primary candidate dataset (upi_transactions_2024.csv):
- failure-rate variance across segments (root-cause feasibility)
- temporal density (temporal feasibility)
- segmentation volume feasibility
- bank-name join-key normalization across datasets
"""
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
pd.set_option("display.max_columns", None)
pd.set_option("display.width", 160)

tx = pd.read_csv(RAW / "UPI Transactions 2024 Dataset" / "upi_transactions_2024.csv")
tx.columns = [c.strip() for c in tx.columns]
tx["timestamp"] = pd.to_datetime(tx["timestamp"])
tx["is_fail"] = (tx["transaction_status"] == "FAILED").astype(int)
overall_fail = tx["is_fail"].mean()

print("=" * 80)
print("A. FAILURE RATE VARIANCE ACROSS SEGMENTS (root-cause / anomaly feasibility)")
print("=" * 80)
print(f"Overall failure rate: {overall_fail*100:.3f}%  (n={len(tx)})")

for col in ["sender_bank", "receiver_bank", "device_type", "network_type",
            "transaction type", "merchant_category", "sender_state",
            "sender_age_group", "hour_of_day", "day_of_week", "is_weekend"]:
    g = tx.groupby(col)["is_fail"].agg(["mean", "count"])
    g["mean"] = g["mean"] * 100
    spread = g["mean"].max() - g["mean"].min()
    print(f"\n--- {col} --- (max-min failure-rate spread: {spread:.3f} pts)")
    print(g.sort_values("mean", ascending=False).round(3).to_string())

print()
print("=" * 80)
print("B. FRAUD FLAG vs FAILURE STATUS relationship (leakage check)")
print("=" * 80)
print(pd.crosstab(tx["fraud_flag"], tx["transaction_status"]))

print()
print("=" * 80)
print("C. TEMPORAL DENSITY (temporal feasibility for incident demo)")
print("=" * 80)
span_days = (tx["timestamp"].max() - tx["timestamp"].min()).days
print(f"Timestamp span: {tx['timestamp'].min()} to {tx['timestamp'].max()} ({span_days} days)")
print(f"Avg transactions/day: {len(tx)/span_days:.1f}")
per_day = tx.set_index("timestamp").resample("D").size()
print(f"Per-day count: min={per_day.min()}, median={per_day.median()}, max={per_day.max()}")
per_hour = tx.set_index("timestamp").resample("h").size()
print(f"Per-hour count: min={per_hour.min()}, median={per_hour.median()}, max={per_hour.max()}, mean={per_hour.mean():.2f}")
per_10min = tx.set_index("timestamp").resample("10min").size()
print(f"Per-10-minute count: min={per_10min.min()}, median={per_10min.median()}, max={per_10min.max()}, mean={per_10min.mean():.2f}")
print(f"Fraction of 10-min windows with 0 transactions: {(per_10min==0).mean()*100:.2f}%")
print(f"Fraction of 10-min windows with <5 transactions: {(per_10min<5).mean()*100:.2f}%")

# duplicate-timestamp-minute check
tx["minute"] = tx["timestamp"].dt.floor("min")
dup_minutes = tx["minute"].duplicated().sum()
print(f"Rows sharing the same to-the-minute timestamp as another row: {dup_minutes} ({dup_minutes/len(tx)*100:.2f}%)")

print()
print("=" * 80)
print("D. SEGMENTATION VOLUME FEASIBILITY (2-way intersections)")
print("=" * 80)
combos = [
    ("sender_bank", "device_type"),
    ("sender_bank", "sender_state"),
    ("sender_bank", "merchant_category"),
    ("device_type", "network_type"),
    ("sender_bank", "network_type"),
    ("sender_state", "merchant_category"),
    ("sender_bank", "transaction type"),
]
for a, b in combos:
    g = tx.groupby([a, b]).size()
    n_cells = g.shape[0]
    n_sparse_30 = (g < 30).sum()
    n_sparse_100 = (g < 100).sum()
    print(f"{a} x {b}: {n_cells} cells, min={g.min()}, median={int(g.median())}, max={g.max()}, "
          f"cells<30={n_sparse_30} ({n_sparse_30/n_cells*100:.0f}%), cells<100={n_sparse_100} ({n_sparse_100/n_cells*100:.0f}%)")

print()
print("=" * 80)
print("E. BANK-NAME JOIN-KEY NORMALIZATION ACROSS DATASETS")
print("=" * 80)
tx_banks = set(tx["sender_bank"].unique()) | set(tx["receiver_bank"].unique())
print(f"Banks in upi_transactions_2024 (sender/receiver, {len(tx_banks)}): {sorted(tx_banks)}")

npci_dir = RAW / "NPCI Products Statistics Since Launch"
remitter = pd.read_csv(npci_dir / "UPI Remitter Banks.csv")
benef = pd.read_csv(npci_dir / "UPI Beneficiary Bank.csv")
psp = pd.read_csv(npci_dir / "UPI Payers Perforance PSP.csv")

remitter_col = [c for c in remitter.columns if "Remitter Bank" in c][0]
benef_col = [c for c in benef.columns if "Beneficiary Banks" in c][0]
psp_col = "Payer PSP"

npci_banks_raw = {b for b in (set(remitter[remitter_col]) | set(benef[benef_col]) | set(psp[psp_col])) if isinstance(b, str)}
print(f"\nDistinct bank/PSP name strings across NPCI Remitter+Beneficiary+PSP files ({len(npci_banks_raw)}):")
for b in sorted(npci_banks_raw):
    print(f"  {b!r}")


def normalize_bank(name: str) -> str:
    n = name.upper()
    n = re.sub(r"\bLTD\.?\b", "", n)
    n = re.sub(r"\bLIMITED\b", "", n)
    n = re.sub(r"[.,]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


tx_norm = {normalize_bank(b): b for b in tx_banks}
npci_norm = {normalize_bank(b): b for b in npci_banks_raw}

print(f"\nAfter normalization (strip Ltd/Limited/punctuation, uppercase):")
matched = []
unmatched = []
for norm, orig in tx_norm.items():
    # try exact normalized match, else substring match
    exact = [no for no in npci_norm if no == norm]
    substr = [no for no in npci_norm if norm in no or no in norm]
    if exact:
        matched.append((orig, npci_norm[exact[0]], "exact"))
    elif substr:
        matched.append((orig, npci_norm[substr[0]], "substring"))
    else:
        unmatched.append(orig)

print(f"Matched {len(matched)}/{len(tx_banks)} tx banks to an NPCI name:")
for m in matched:
    print(f"  {m[0]!r:20s} -> {m[1]!r}  [{m[2]}]")
print(f"Unmatched tx banks ({len(unmatched)}): {unmatched}")

print()
print("=" * 80)
print("F. TRANSACTION-ID OVERLAP BETWEEN THE TWO TRANSACTION-LEVEL DATASETS")
print("=" * 80)
insights = pd.read_csv(RAW / "UPI Transaction Insights Dataset" / "upi_transaction_insights_dataset.csv")
ids_tx = set(tx["transaction id"] if "transaction id" in tx.columns else tx["transaction_id"])
ids_insights = set(insights["transaction_id"])
overlap = ids_tx & ids_insights
print(f"upi_transactions_2024 id format sample: {list(ids_tx)[:3]}")
print(f"upi_transaction_insights id format sample: {list(ids_insights)[:3]}")
print(f"Exact ID overlap: {len(overlap)} ids")

print()
print("=" * 80)
print("G. DATE-RANGE OVERLAP CHECK: insights dataset vs transactions_2024")
print("=" * 80)
insights["date"] = pd.to_datetime(insights["date"])
print(f"insights date range: {insights['date'].min()} to {insights['date'].max()}")
print(f"tx2024 date range: {tx['timestamp'].min()} to {tx['timestamp'].max()}")
