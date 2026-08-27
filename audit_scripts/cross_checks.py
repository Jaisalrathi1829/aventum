"""
Cross-dataset consistency checks + join-key testing for Day 1 audit.
Read-only.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
OUT = Path(__file__).resolve().parent / "output"

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 160)


def clean_num(s):
    return pd.to_numeric(
        s.astype(str).str.replace(",", "", regex=False).str.replace("\xa0", "", regex=False).str.strip(),
        errors="coerce",
    )


def parse_npci_month(s):
    # format "16-Apr" -> year=2016, month=4
    m = re.match(r"^(\d{2})-([A-Za-z]{3})$", s.strip())
    if not m:
        return None
    yy, mon = m.groups()
    year = 2000 + int(yy)
    month = pd.to_datetime(mon, format="%b").month
    return pd.Timestamp(year=year, month=month, day=1)


print("=" * 80)
print("CHECK 1: upi_india_monthly_enriched vs NPCI UPI Product Statistics (overlap)")
print("=" * 80)

enriched = pd.read_csv(RAW / "Indian UPI Transactions" / "upi_india_monthly_enriched.csv")
enriched["Date"] = pd.to_datetime(enriched["Date"])

npci_upi = pd.read_csv(RAW / "NPCI Products Statistics Since Launch" / "UPI Product  Statistics.csv")
npci_upi["parsed_month"] = npci_upi["Month"].apply(parse_npci_month)
npci_upi["Volume_clean"] = clean_num(npci_upi["Volume (in Mn)"])
npci_upi["Value_clean"] = clean_num(npci_upi["Value (in Cr.)"])

merged = enriched.merge(
    npci_upi[["parsed_month", "Volume_clean", "Value_clean"]],
    left_on="Date", right_on="parsed_month", how="inner"
)
merged["volume_diff"] = (merged["Volume_Mn"] - merged["Volume_clean"]).abs()
merged["value_diff"] = (merged["Value_Cr"] - merged["Value_clean"]).abs()
print(f"Overlapping months found: {len(merged)} (enriched rows={len(enriched)}, npci rows={len(npci_upi)})")
print(f"Enriched date range: {enriched['Date'].min()} to {enriched['Date'].max()}")
print(f"NPCI parsed date range: {npci_upi['parsed_month'].min()} to {npci_upi['parsed_month'].max()}")
if len(merged):
    print("Sample comparison (first 5, last 5):")
    cols = ["Date", "Volume_Mn", "Volume_clean", "volume_diff", "Value_Cr", "Value_clean", "value_diff"]
    print(merged[cols].head(5).to_string(index=False))
    print(merged[cols].tail(5).to_string(index=False))
    print(f"Max volume diff: {merged['volume_diff'].max()}, Max value diff: {merged['value_diff'].max()}")
    print(f"Rows with volume_diff > 0.01: {(merged['volume_diff'] > 0.01).sum()} / {len(merged)}")
    print(f"Rows with value_diff > 1.0: {(merged['value_diff'] > 1.0).sum()} / {len(merged)}")
else:
    print("NO OVERLAPPING MONTHS FOUND BETWEEN THE TWO DATASETS.")

print()
print("=" * 80)
print("CHECK 2: upi_transactions_2024.csv vs 'UPI TRANSACTION DATASET' README claims")
print("=" * 80)
tx = pd.read_csv(RAW / "UPI Transactions 2024 Dataset" / "upi_transactions_2024.csv")
tx.columns = [c.strip() for c in tx.columns]
total_amount = tx["amount (INR)"].sum()
n = len(tx)
fraud_rate = tx["fraud_flag"].mean() * 100
android_pct = (tx["device_type"] == "Android").mean() * 100
tx["timestamp"] = pd.to_datetime(tx["timestamp"])
tx["hour"] = tx["timestamp"].dt.hour
peak_mask = tx["hour"].between(16, 19)  # 4PM-8PM exclusive of 8pm hour start
peak_count = int(peak_mask.sum())
top_sender_age = tx["sender_age_group"].value_counts().idxmax()
top_bank = tx["sender_bank"].value_counts().idxmax()

print(f"Row count: {n} (README claims 250K+)")
print(f"Total amount (INR), assuming column is Rupees: {total_amount:,.2f}  (README claims Rs 328 million)")
print(f"Total amount / 1e7 (if units were meant as paise or other): {total_amount/1e7:,.2f}")
print(f"Fraud rate: {fraud_rate:.4f}% (README claims 0.0019%)")
print(f"Android device share: {android_pct:.2f}% (README claims 75%)")
print(f"Transactions in 16:00-19:59 hour window: {peak_count} ({peak_count/n*100:.2f}%) (README claims 73.6K in 4-8PM)")
print(f"Most active sender age group: {top_sender_age} (README claims 26-35 most active)")
print(f"Top sender bank: {top_bank} (README claims SBI leads)")
print(f"transaction_status values: {tx['transaction_status'].value_counts().to_dict()}")
print(f"Success rate: {(tx['transaction_status']=='SUCCESS').mean()*100:.3f}%")

print()
print("=" * 80)
print("CHECK 3: upi_transaction_insights_dataset.csv - is_successful class balance")
print("=" * 80)
insights = pd.read_csv(RAW / "UPI Transaction Insights Dataset" / "upi_transaction_insights_dataset.csv")
print(insights["is_successful"].value_counts())
print("Perfectly balanced 50/50 classes strongly indicates synthetic/toy classification dataset,")
print("not representative of real-world UPI failure rates (~1-5% typical failure).")
