"""
Day 1.5 audit: initial structural inspection of the newly added Nigerian Card
Payment Dataset for Predictive Routing (parquet). Read-only.
"""
import sys
from pathlib import Path

import pyarrow.parquet as pq

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
FILE = RAW / "Nigerian Card Payment Dataset for Predictive Routing.parquet"

pf = pq.ParquetFile(FILE)
schema = pf.schema_arrow
meta = pf.metadata

print("=" * 80)
print("FILE-LEVEL PARQUET METADATA")
print("=" * 80)
print(f"File: {FILE.name}")
print(f"Size on disk: {FILE.stat().st_size:,} bytes")
print(f"Num row groups: {meta.num_row_groups}")
print(f"Num rows: {meta.num_rows:,}")
print(f"Num columns: {meta.num_columns}")
print(f"Created by: {meta.created_by}")
print(f"Format version: {meta.format_version}")

print()
print("=" * 80)
print("KEY-VALUE METADATA (may carry HuggingFace dataset-card / provenance info)")
print("=" * 80)
kv = schema.metadata
if kv:
    for k, v in kv.items():
        key_str = k.decode("utf-8", errors="replace") if isinstance(k, bytes) else k
        val_str = v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v
        print(f"--- {key_str} ---")
        print(val_str[:3000])
        print()
else:
    print("No key-value metadata found in the file.")

print()
print("=" * 80)
print("ARROW SCHEMA (column name -> type, as stored in the parquet file)")
print("=" * 80)
for field in schema:
    print(f"  {field.name!r}: {field.type}  (nullable={field.nullable})")

print()
print("=" * 80)
print("PER-COLUMN PARQUET STATISTICS (from row group metadata, row group 0)")
print("=" * 80)
rg0 = meta.row_group(0)
for i in range(rg0.num_columns):
    col_meta = rg0.column(i)
    stats = col_meta.statistics
    print(f"  {col_meta.path_in_schema!r}: encoding={col_meta.encodings}, "
          f"compressed_size={col_meta.total_compressed_size}, "
          f"has_stats={stats is not None}")
    if stats is not None:
        print(f"       min={stats.min!r} max={stats.max!r} null_count={stats.null_count} "
              f"distinct_count={stats.distinct_count} num_values={stats.num_values}")
