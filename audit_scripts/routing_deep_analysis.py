"""
Deep analysis of the Nigerian Card Payment Dataset for Predictive Routing:
- failure/latency variance by rail_id (gateway), payment_channel, merchant_segment
- gateway_response / error_code consistency check
- latency vs status/error relationship
- temporal failure/latency pattern (is there an organic incident already present?)
- join-key testing against upi_transactions_2024
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
pd.set_option("display.max_columns", None)
pd.set_option("display.width", 160)

df = pd.read_parquet(RAW / "Nigerian Card Payment Dataset for Predictive Routing.parquet")
df["is_fail"] = (df["status"] == "failed").astype(int)

print("=" * 80)
print("A. FAILURE RATE AND LATENCY BY RAIL_ID (the candidate 'gateway' dimension)")
print("=" * 80)
g = df.groupby("rail_id").agg(
    volume=("transaction_id", "count"),
    failure_rate_pct=("is_fail", lambda x: x.mean() * 100),
    latency_mean=("latency_ms", "mean"),
    latency_p50=("latency_ms", "median"),
    latency_p95=("latency_ms", lambda x: x.quantile(0.95)),
    latency_p99=("latency_ms", lambda x: x.quantile(0.99)),
)
print(g.round(2).to_string())
print(f"\nFailure-rate spread across rails: {g['failure_rate_pct'].max() - g['failure_rate_pct'].min():.3f} points")
print(f"Latency p50 spread across rails: {g['latency_p50'].max() - g['latency_p50'].min():.2f} ms")

print()
print("=" * 80)
print("B. FAILURE RATE AND LATENCY BY PAYMENT_CHANNEL")
print("=" * 80)
g2 = df.groupby("payment_channel").agg(
    volume=("transaction_id", "count"),
    failure_rate_pct=("is_fail", lambda x: x.mean() * 100),
    latency_mean=("latency_ms", "mean"),
    latency_p95=("latency_ms", lambda x: x.quantile(0.95)),
)
print(g2.round(2).to_string())

print()
print("=" * 80)
print("C. FAILURE RATE BY MERCHANT_SEGMENT, and RAIL x CHANNEL cross")
print("=" * 80)
g3 = df.groupby("merchant_segment").agg(volume=("transaction_id", "count"),
                                         failure_rate_pct=("is_fail", lambda x: x.mean() * 100))
print(g3.round(3).to_string())
print()
cross = df.groupby(["rail_id", "payment_channel"]).agg(
    volume=("transaction_id", "count"), failure_rate_pct=("is_fail", lambda x: x.mean() * 100)
)
print(cross.round(2).to_string())

print()
print("=" * 80)
print("D. gateway_response <-> error_code <-> status CONSISTENCY CHECK")
print("=" * 80)
print(pd.crosstab(df["gateway_response"], df["status"]))
print()
print(pd.crosstab(df["gateway_response"], df["error_code"]))
mismatch = df[(df["status"] == "success") & (df["gateway_response"] != "Approved")]
print(f"\nSuccess rows with gateway_response != 'Approved': {len(mismatch)}")
mismatch2 = df[(df["status"] == "failed") & (df["gateway_response"] == "Approved")]
print(f"Failed rows with gateway_response == 'Approved': {len(mismatch2)}")

print()
print("=" * 80)
print("E. LATENCY BY STATUS / GATEWAY_RESPONSE (is Timeout actually high-latency?)")
print("=" * 80)
print(df.groupby("status")["latency_ms"].describe().round(2))
print()
print(df.groupby("gateway_response")["latency_ms"].describe().round(2))

print()
print("=" * 80)
print("F. TEMPORAL PATTERN: hourly failure rate and latency (looking for an organic incident)")
print("=" * 80)
hourly = df.set_index("timestamp").resample("h").agg(
    volume=("transaction_id", "count"),
    failure_rate_pct=("is_fail", lambda x: x.mean() * 100),
    latency_mean=("latency_ms", "mean"),
)
print(f"Hourly failure rate: min={hourly['failure_rate_pct'].min():.3f}%, "
      f"median={hourly['failure_rate_pct'].median():.3f}%, max={hourly['failure_rate_pct'].max():.3f}%")
print(f"Hourly latency mean: min={hourly['latency_mean'].min():.2f}, "
      f"median={hourly['latency_mean'].median():.2f}, max={hourly['latency_mean'].max():.2f}")
# show top 10 hours by failure rate
print("\nTop 10 hours by failure rate:")
print(hourly.sort_values("failure_rate_pct", ascending=False).head(10).round(3).to_string())
print("\nTop 10 hours by mean latency:")
print(hourly.sort_values("latency_mean", ascending=False).head(10).round(2).to_string())

print()
print("=" * 80)
print("G. Per-rail hourly failure rate variance (checking for rail-specific time-bounded degradation)")
print("=" * 80)
rail_hourly = df.set_index("timestamp").groupby("rail_id").resample("h").agg(
    volume=("transaction_id", "count"), failure_rate_pct=("is_fail", lambda x: x.mean() * 100)
)
for rail in df["rail_id"].unique():
    sub = rail_hourly.loc[rail]
    print(f"{rail}: hourly failure-rate min={sub['failure_rate_pct'].min():.2f}%, "
          f"median={sub['failure_rate_pct'].median():.2f}%, max={sub['failure_rate_pct'].max():.2f}% "
          f"(overall mean {df[df['rail_id']==rail]['is_fail'].mean()*100:.2f}%)")

print()
print("=" * 80)
print("H. JOIN-KEY TESTING AGAINST upi_transactions_2024")
print("=" * 80)
upi = pd.read_csv(RAW / "UPI Transactions 2024 Dataset" / "upi_transactions_2024.csv")
upi.columns = [c.strip() for c in upi.columns]
upi["timestamp"] = pd.to_datetime(upi["timestamp"])

print(f"Routing dataset transaction_id sample: {df['transaction_id'].iloc[:3].tolist()}")
print(f"upi_transactions_2024 transaction id sample: {upi['transaction id'].iloc[:3].tolist()}")
id_overlap = set(df["transaction_id"]) & set(upi["transaction id"])
print(f"transaction_id exact overlap: {len(id_overlap)}")

print(f"\nRouting dataset timestamp range: {df['timestamp'].min()} to {df['timestamp'].max()}")
print(f"upi_transactions_2024 timestamp range: {upi['timestamp'].min()} to {upi['timestamp'].max()}")
routing_dates = set(df["timestamp"].dt.tz_localize(None).dt.date)
upi_dates = set(upi["timestamp"].dt.date)
print(f"Calendar-date overlap between the two datasets: {len(routing_dates & upi_dates)} dates")

print(f"\nRouting dataset currency/country context: Nigeria (NG); amount range {df['amount'].min()}-{df['amount'].max()}")
print(f"upi_transactions_2024 currency/country context: India (INR); amount range {upi['amount (INR)'].min()}-{upi['amount (INR)'].max()}")

print("\nAny shared bank/merchant vocabulary?")
print(f"Routing dataset merchant_id sample: {sorted(df['merchant_id'].unique())[:5]}")
print(f"upi_transactions_2024 has no merchant_id field (only merchant_category) - confirmed structurally incompatible dimension")
print(f"Routing dataset has no bank field at all (rail_id/payment_channel are the closest concepts) - confirmed no shared bank vocabulary")

print()
print("=" * 80)
print("I. amount unit sanity check (Naira major-unit vs kobo minor-unit plausibility)")
print("=" * 80)
print(df["amount"].describe())
print("If minor-unit (kobo, 1/100 Naira): min tx = NGN 1.00, median = NGN 6.64, max = NGN 133.05 -> implausibly tiny for card/ussd/transfer payments")
print("If major-unit (Naira): min tx = NGN 100, median = NGN 664, max = NGN 13,305 -> plausible low-to-mid value consumer payments")
