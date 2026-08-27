import pandas as pd
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
df = pd.read_parquet(RAW / "Nigerian Card Payment Dataset for Predictive Routing.parquet")
df["is_fail"] = (df["status"] == "failed").astype(int)

rail_hourly = df.set_index("timestamp").groupby("rail_id").resample("h").agg(
    volume=("transaction_id", "count"), failures=("is_fail", "sum")
)
rail_hourly["failure_rate_pct"] = rail_hourly["failures"] / rail_hourly["volume"] * 100

print("Per-rail: hour of max failure rate, and what other rails looked like at that same hour")
for rail in sorted(df["rail_id"].unique()):
    sub = rail_hourly.loc[rail]
    top_hour = sub["failure_rate_pct"].idxmax()
    print(f"\n{rail}: worst hour = {top_hour}, failure_rate={sub.loc[top_hour, 'failure_rate_pct']:.2f}%, volume={sub.loc[top_hour, 'volume']}")
    snapshot = rail_hourly.xs(top_hour, level=1)
    print(snapshot.round(2).to_string())

print()
print("=== Spike hours per rail (>3x that rail's own median hourly failure rate) ===")
for rail in sorted(df["rail_id"].unique()):
    sub = rail_hourly.loc[rail]
    thresh = sub["failure_rate_pct"].median() * 3
    spikes = sub[sub["failure_rate_pct"] > thresh]
    print(f"{rail}: median={sub['failure_rate_pct'].median():.2f}%, threshold(3x)={thresh:.2f}%, spike hours={len(spikes)} of {len(sub)}")
    for ts, row in spikes.iterrows():
        print(f"    {ts}  failure_rate={row['failure_rate_pct']:.2f}%  volume={row['volume']:.0f}")
