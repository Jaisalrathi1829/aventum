# Routing Dataset Audit — Nigerian Card Payment Dataset for Predictive Routing

Targeted integration/feasibility audit of the newly added raw file. Computed via `audit_scripts/inspect_routing_parquet.py`, `profile_routing_dataset.py`, `routing_deep_analysis.py`, `routing_spike_check.py` (read-only; `data/raw/` untouched; nothing merged with `upi_transactions_2024`). This dataset is treated as fully untrusted throughout — every claim below is measured, not assumed from column names.

---

## 1. Identification

| Property | Value |
|---|---|
| Filename | `Nigerian Card Payment Dataset for Predictive Routing.parquet` |
| Format | Apache Parquet, format version 2.6, written by `parquet-cpp-arrow 21.0.0` via `pandas 2.3.2` (embedded metadata confirms a `pandas.to_parquet()` export — the file was produced programmatically, not hand-assembled) |
| Size on disk | 61,352,352 bytes (~58.5 MiB), compressed columnar |
| Source path | `data/raw/Nigerian Card Payment Dataset for Predictive Routing.parquet` — sits directly at the top level of `data/raw/`, unlike every Day 1 dataset which lived in its own subfolder. Noted as a structural inconsistency; not corrected, per the no-modification rule. |
| Row count | 2,026,891 |
| Column count | 14 |
| Locally available metadata | Only the embedded Arrow/pandas schema block (column names, pandas dtypes, timezone note, pyarrow/pandas version). **No README, dataset card, LICENSE, or data dictionary was downloaded alongside the file** — confirmed by a filename search across `data/raw/` for anything Nigeria/routing-related. Any licensing, collection-methodology, or source-attribution information that a Hugging Face dataset card would normally carry is **not present locally** and is therefore unverifiable in this audit. |
| Row groups | 2 (1,048,576 + 978,315 rows) |

### Schema

| Column | Type | Nulls | Notes |
|---|---|---|---|
| `timestamp` | timestamp(ns, UTC) | 0 | Minute precision only — seconds and microseconds are always `:00` (verified: `(df.timestamp.dt.second==0).all() == True`). Coarser than `upi_transactions_2024`'s second precision. |
| `transaction_id` | string | 0 | Format `tx_<int>`, sequential **1 to 2,026,891 with zero gaps** — every integer in range appears exactly once. |
| `reference` | string | 0 | Format `ref_<12-hex-chars>`, fixed 16-character length, 100% unique. A random-hex reference code, functionally redundant with `transaction_id` as an identifier. |
| `merchant_id` | string | 0 | Format `m_0000`–`m_0299`, 300 distinct values. |
| `merchant_segment` | string | 0 | 3 values: `MID`, `ENT`, `SME` — read as merchant-size segments (Mid-market / Enterprise / Small-Medium-Enterprise), **not confirmed by any in-file legend**. |
| `rail_id` | string | 0 | 5 values: `rail_A`…`rail_E`. |
| `payment_channel` | string | 0 | 4 values: `card`, `mobile_money`, `ussd`, `transfer`. |
| `amount` | int64 | 0 | 100–13,305, no currency field. |
| `status` | string | 0 | 2 values: `success`, `failed`. |
| `gateway_response` | string | 0 | 6 values: `Approved`, `Insufficient Funds`, `Processing Error`, `Issuer Declined`, `Do Not Honor`, `Timeout`. |
| `error_code` | string | 0 (structural) | 6 values including an **empty string** used as the "no error" sentinel for successful transactions — a semantic null hidden inside a non-null field; pandas' `isna()` does not catch this. |
| `latency_ms` | float64 | 0 | 50.0–6,891.14. |
| `region` | string | 0 | **Single constant value `NG`** across all 2,026,891 rows — carries zero information despite the field name implying sub-national geography. |
| `country` | string | 0 | Single constant value `NG`. |

### Candidate keys

`transaction_id` (100% unique, 1:1 with rows) and `reference` (100% unique, 1:1 with rows) are both valid primary-key candidates; they are two independent identifier schemes for the same row, not a parent/child relationship.

### Duplicates

0 fully duplicate rows.

### Date/time range and granularity

`2025-01-01 00:00:00 UTC` to `2025-01-07 23:59:00 UTC` — almost exactly 7 days. Minute-granularity timestamps (see schema table). Density is far higher than `upi_transactions_2024`: **~289,600 rows/day, ~201 rows/minute on average (range 156–255/minute)** — roughly 42× the daily volume and, per-minute, roughly two orders of magnitude denser than the UPI dataset's ~0.5 tx/minute average.

### Categorical distributions

See the schema table for value sets; full frequency counts are in §12 and `audit_scripts/output/routing_deep_analysis_output.txt`.

### Numeric distributions

`amount`: mean 795.82, median 664, p99 2,685, max 13,305, no negative/zero values. `latency_ms`: mean 513.19, median 503.74, p99 993.59, max 6,891.14, no negative/zero values, no nulls.

### Suspicious / impossible values

No impossible values (no negative amounts or latencies, no out-of-range timestamps). Several findings point strongly toward **synthetic/simulated generation** rather than raw production telemetry — listed together here since each was measured, not assumed:

1. **`transaction_id` is a perfectly sequential integer run (1…2,026,891) with zero gaps.** Real production transaction counters almost never have zero gaps at this volume (test transactions, sharding, cancellations, retries all typically create gaps).
2. **`merchant_id` transaction counts are unnaturally uniform**: 300 merchants, mean 6,756 transactions each, std only 80.9 (≈1.2% relative std). Real merchant populations are heavily right-skewed (a small number of merchants dominate volume); this is a flat/uniform allocation instead.
3. **`status` / `gateway_response` / `error_code` are perfectly deterministic against each other with zero exceptions across 2,026,891 rows** (verified via crosstab, §12) — every real-world payment log has at least some edge-case inconsistency (late-arriving status corrections, ambiguous codes); a completely clean 1:1:1 mapping at this volume is a generation-template signature.
4. **The `Timeout` latency floor sits at exactly 2,000.0 ms with no `Timeout`-labeled row below it**, while all other outcomes cap out at 1,824 ms or below — consistent with a modeled rule ("if simulated latency > 2000ms, label as Timeout") rather than an observed threshold.
5. **`region`/`country` are constant single values** despite field names that imply real variation.

None of this makes the dataset useless — it makes it a **structurally clean, internally consistent synthetic dataset**, which is exactly what §4 formalizes.

---

## 2. Dataset Grain

**One row = one completed payment transaction, on exactly one rail, with exactly one recorded outcome.** Verified computationally, not assumed:

- `transaction_id` occurs **exactly once per row for all 2,026,891 rows** (`min=median=mean=max=1` repetitions) — there is no case of a single `transaction_id` appearing more than once.
- `reference` is independently 100% unique as well.
- Therefore: **one transaction cannot have multiple rows, one transaction cannot have multiple attempts, and no transaction shows more than one gateway/rail.** Each row is a single, final, immutable attempt-and-outcome record.

**Direct answers to the brief's grain questions:**

| Question | Answer |
|---|---|
| Can one transaction have multiple rows? | **No** — confirmed 1:1. |
| Can one transaction have multiple attempts? | **No** — there is no attempt-sequence field, no parent-transaction-id field, and no retry-count field anywhere in the 14 columns. If retries happen upstream of this data, they are invisible here. |
| Can multiple gateways be represented for one transaction? | **No** — `rail_id` is a single value per row; the dataset never shows the same transaction routed to two different rails. |
| Are routing decisions explicit? | **No** — `rail_id` is a *recorded outcome attribute* of the transaction, not a decision event with its own timestamp, reasoning, or alternative-considered field. There is no routing-policy or routing-decision table/column. |
| Is latency measured per transaction or per route? | **Per transaction** — since each transaction touches exactly one rail, `latency_ms` is indistinguishable as "per-transaction" vs. "per-route"; they are the same thing here. |
| Do error codes correspond to individual attempts? | **Yes, 1:1 with the single attempt each row represents** — not an attempt-level log within a multi-attempt transaction, because no such multi-attempt structure exists. |

**Consequence for Aventum's counterfactual simulator (flagged now, developed fully in §8):** this dataset **never observes the same transaction on two different rails**. Any "what would have happened on rail B instead of rail A" comparison must be a **between-subjects comparison of different transactions on different rails**, not a matched counterfactual pair for the same transaction. This is a real limitation, not a data-quality defect — it is simply what a single-attempt-per-transaction dataset can and cannot support.

---

## 3. Full Field Semantics

Determined from actual value behavior (distributions, cross-tabulations, correlations — §12/§D of the deep-analysis output), not inferred from names alone.

| Field | Exact meaning (as measured) | Likely unit | Observed or derived | Transaction state or infrastructure state | Pre- or post-outcome | Real-time-safe? | Safe as simulator input? |
|---|---|---|---|---|---|---|---|
| `timestamp` | Minute the transaction was recorded | UTC minute | Observed | Transaction | Pre-outcome (transaction moment) | Yes | Yes |
| `transaction_id` | Sequential synthetic identifier | — | Observed (as generated) | Transaction | N/A | Yes | Yes (as a key, not a feature) |
| `reference` | Redundant random-hex identifier | — | Observed (as generated) | Transaction | N/A | Yes | No (adds nothing `transaction_id` doesn't already give) |
| `merchant_id` | Anonymized merchant identifier, 300 distinct | — | Observed | Transaction | Pre-outcome | Yes | Yes |
| `merchant_segment` | Merchant size/type bucket (MID/ENT/SME, inferred meaning, unconfirmed) | — | Observed | Transaction | Pre-outcome | Yes | Yes, with the "inferred, unconfirmed" caveat carried forward |
| `rail_id` | **The processing rail/gateway the transaction was routed through** — measurably drives real failure-rate variance (§7) | — | Observed | **Infrastructure** | Pre-outcome (routing already decided by the time the row exists) | Yes | **Yes — the dataset's single strongest infrastructure signal** |
| `payment_channel` | Consumer-facing payment method (card/mobile_money/ussd/transfer) | — | Observed | Transaction | Pre-outcome | Yes | Yes, though it carries almost no failure-rate signal (§7) |
| `amount` | Transaction value | Assessed as Naira (major unit) — see §1 unit-plausibility check; **not confirmed by any currency field** | Observed | Transaction | Pre-outcome | Yes | Yes, with the unit caveat disclosed |
| `status` | Binary transaction outcome | — | Observed | Transaction | **Post-outcome** | No — not knowable before the transaction resolves | Yes, as a *label*, never as a live-scoring input feature |
| `gateway_response` | Human-readable outcome/decline reason | — | Observed | **Infrastructure** (gateway-issued) | Post-outcome | No | Yes, as a label/evidence field |
| `error_code` | Machine-readable outcome/decline reason — **perfectly redundant with `gateway_response`** (verified 1:1 crosstab, zero exceptions) | — | Observed | Infrastructure | Post-outcome | No | Yes, as a label; do not treat as a second independent signal from `gateway_response` |
| `latency_ms` | Processing time for the transaction | milliseconds | Observed | **Infrastructure** | **Post-outcome** (the full round-trip time is only known once the transaction resolves) | No — the final value isn't known until resolution, though a live system could stream partial/interim latency | Yes — this is the dataset's second-strongest infrastructure signal (§11) |
| `region` | Constant `NG` — carries no variation | — | Observed (but degenerate) | N/A | N/A | N/A | **No — zero information content** |
| `country` | Constant `NG` — carries no variation | — | Observed (but degenerate) | N/A | N/A | N/A | **No — zero information content** |

**Key semantic finding:** of the 14 columns, exactly **two carry genuine infrastructure-state information with measurable analytical value**: `rail_id` (routing/gateway identity) and `latency_ms` (processing time). `gateway_response`/`error_code` are a single post-outcome label duplicated across two encodings. `region`/`country` are present in name only.

---

## 4. Data Quality and Realism

**Missingness:** 0 nulls in every column (structural). One semantic null hidden as an empty string in `error_code` for all 1,972,633 successful rows (matches the `Approved`/`success` count exactly).

**Duplicates:** 0 duplicate rows; both `transaction_id` and `reference` are independently 100% unique.

**Repeated IDs:** none — see §2.

**Impossible values:** none found (no negative/zero amounts, no negative/zero latency, no out-of-range dates, no unrecognized status/gateway_response/error_code combinations).

**Response-code / error-code consistency:** **perfect** — `status`, `gateway_response`, and `error_code` form a single deterministic mapping with zero exceptions across all 2,026,891 rows (full crosstab in `audit_scripts/output/routing_deep_analysis_output.txt` §D):

- `status=success` ⟺ `gateway_response=Approved` ⟺ `error_code=''` — always, no exceptions.
- Each of the 5 failure `gateway_response` values maps 1:1 to exactly one `error_code` value, and only ever co-occurs with `status=failed`.

**Latency distribution:** realistic overall shape (right-skewed, p50 503.7ms, p99 993.6ms) with a **structurally distinct high-latency tail for `Timeout`** (mean 3,986ms, min 2,000ms, max 6,891ms) — full detail in §11.

**Gateway/rail distributions:** 5 rails, traffic split 245K–563K each (not perfectly even, but all substantial); full detail in §12.

**Failure distributions:** overall 2.68% (54,258/2,026,891) — realistic order of magnitude for card/digital payments.

**Temporal density:** ~201 rows/minute average (range 156–255) — dense enough to support genuinely minute-level statistical analysis, unlike `upi_transactions_2024`.

**Correlations between status, latency, error, and rail — all measured (§7, §11, §12):**
- Latency correlates strongly and sensibly with outcome type: success ≈500ms, non-timeout failures ≈900ms (~1.8×), timeout failures ≈3,986ms (~8×) with a clean 2,000ms floor.
- `rail_id` correlates meaningfully with failure rate (1.85%–3.83% across rails, a genuine ~2× spread) but only trivially with latency (p50 spread of 3.18ms across rails).
- `payment_channel` shows almost no failure-rate variance (2.62%–2.69% — a 0.07-point spread, i.e., no real signal).
- Per-rail, per-hour failure rate shows **large, isolated, single-rail-at-a-time spikes** (§9) — each rail has 3–5 hours (out of 168) where its own failure rate jumps to 12–26% while every other rail stays within its normal 1–4% band at that same hour (verified in `routing_spike_check.py` output — confirmed rail-isolated, not systemic).

**Real / synthetic / simulated / derived / unclear:** **assessed as synthetic/simulated, with high confidence**, based on the cumulative evidence in §1 (gapless sequential IDs, unnaturally uniform merchant volumes, perfect status/response/error determinism, a suspiciously round 2,000ms timeout floor) plus the isolated-rail-spike pattern in §9, which reads as a deliberately parameterized data-generation process (consistent with the dataset's own name — "for Predictive Routing" implies it was built to train/evaluate a routing model, which is exactly the kind of synthetic data generation that would inject clean, learnable, rail-specific degradation episodes). **This assessment must not be upgraded to "real production evidence" anywhere downstream** — it is used in this audit strictly as a well-constructed synthetic reference, per the task's explicit instruction.

---

## 5. Aventum Field Mapping

Against the Day 1 gap list (gateway, routing_path, routing_policy, gateway_latency, gateway_response, error_code, gateway_health, retry_count, failure_reason, incident metadata):

| Aventum field | Classification | Usability | Reasoning |
|---|---|---|---|
| `gateway` | **STRONG PROXY** (`rail_id`) | **1. OBSERVED INPUT** (within this dataset's own population only) / **2. SIMULATION CALIBRATION** (for Aventum's synthetic gateway layer) | `rail_id` is a genuine, measurably differentiated routing-endpoint identifier — the single best find in this dataset. Not an EXACT FIELD because it's called "rail" (Nigerian card-scheme terminology) rather than "gateway," and because it cannot be attached to any UPI transaction (§6). |
| `routing_path` | **INVALID / NO PROXY** | 5. NOT USEFUL | No field records a path/sequence — `rail_id` is a flat outcome attribute, not a path. |
| `routing_policy` | **INVALID / NO PROXY** | 5. NOT USEFUL | No policy/decision-rule field exists; routing is implicit and immutable per transaction (§2). |
| `gateway_latency` | **STRONG PROXY** (`latency_ms`) | **2. SIMULATION CALIBRATION** / **4. SYNTHETIC GENERATION TEMPLATE** | Realistic, well-behaved distribution with a genuine outcome-dependent structure (§11) — excellent template for parameterizing Aventum's own synthetic latency model, but itself synthetic, not observed real infrastructure. |
| `gateway_response` | **EXACT FIELD** (name match) | **3. REFERENCE / VOCABULARY** | The 6-value vocabulary (`Approved`, `Insufficient Funds`, `Processing Error`, `Issuer Declined`, `Do Not Honor`, `Timeout`) is a plausible, usable label set — but see §10 for the "opaque vs. documented" caveat. |
| `error_code` | **EXACT FIELD** (name match), but **redundant with `gateway_response`** | **3. REFERENCE / VOCABULARY** | Machine-readable twin of `gateway_response` (snake_case); carries no independent information (§4). |
| `gateway_health` | **WEAK PROXY** (derivable, not observed) | **2. SIMULATION CALIBRATION** | No explicit health-state field exists, but a rolling per-rail failure-rate/latency computation over this data *could* serve as a proxy health signal within this dataset's own population — never observed directly. |
| `retry_count` | **INVALID / NO PROXY** | 5. NOT USEFUL | No retry/attempt-sequence field exists anywhere (§2). |
| `failure_reason` | **STRONG PROXY** (`gateway_response`/`error_code`) | **3. REFERENCE / VOCABULARY** | Same vocabulary as `gateway_response`; see §10 for classification-of-meaning detail. |
| `incident metadata` (id/start/end/type/affected_segment/root_cause) | **WEAK PROXY** (the rail-isolated spike windows found in §9 look like implicit incidents) | **4. SYNTHETIC GENERATION TEMPLATE** | No explicit incident label exists anywhere in the schema — but the measured spike pattern (§9) is a genuinely useful **shape template** for what a realistic, isolated, rail-specific incident should look like (magnitude, duration, latency co-movement) when Aventum designs its own labeled incident-injection layer. It is not itself ground truth for anything, since these windows are not labeled as incidents by the source data — they were *discovered* by this audit, not documented by the dataset. |

**No field in this dataset is directly attachable to any specific `upi_transactions_2024` row** — every use above is either within-dataset (as its own observed population) or as a calibration/template input to Aventum's synthetic layer. This is developed fully in §6 and §8.

---

## 6. Can It Be Legitimately Joined to `upi_transactions_2024`?

**No. Every candidate key was tested computationally and every one fails outright — this is a more thorough non-match than any Day 1 join test.**

| Candidate key | Test result |
|---|---|
| Transaction ID | `transaction_id` formats are structurally incompatible (`tx_1`…`tx_2026891` vs. `TXN0000000001`…) and **exact-match overlap = 0** of 2,026,891 × 250,000 possible pairs. |
| Timestamp / date | Routing dataset spans **2025-01-01 to 2025-01-07**; `upi_transactions_2024` spans **2024-01-01 to 2024-12-30**. **Calendar-date overlap = 0 dates.** No temporal join of any kind (exact, same-minute, same-hour, same-day, nearest-within-tolerance) can produce a single valid pair — the two datasets do not share a single day, let alone a minute. |
| Amount | Different currencies/scales (Nigerian Naira, 100–13,305 vs. Indian Rupees, 10–42,099) with no stated FX-conversion basis; amount was not tested as a join key because even if numerically overlapping ranges existed, amount alone is never a valid entity key (would fabricate arbitrary pairings, exactly the fabrication the task warns against). |
| Payment method / channel | `payment_channel` (card/mobile_money/ussd/transfer) is a different vocabulary from `upi_transactions_2024`'s "transaction type" (P2P/P2M/Bill Payment/Recharge) — these describe different concepts (settlement channel vs. UPI transaction purpose) and share no controlled vocabulary. |
| Bank | The routing dataset **has no bank field at all** — `rail_id` and `payment_channel` are the closest concepts, and neither is a bank identifier. `upi_transactions_2024`'s `sender_bank`/`receiver_bank` (Indian bank short names) have no counterpart here whatsoever. |
| Merchant | Routing dataset `merchant_id` (`m_0000`–`m_0299`, 300 anonymized IDs) vs. `upi_transactions_2024`'s `merchant_category` (10 named categories, no merchant IDs) — different grain and different vocabulary; no overlap is testable, let alone present. |
| Geography | Routing dataset `region`/`country` are both constant `NG` (Nigeria); `upi_transactions_2024.sender_state` is a set of 10 Indian states. Disjoint by construction (different countries) — zero overlap possible. |

**Conclusion: NO LEGITIMATE ROW-LEVEL JOIN EXISTS between this dataset and `upi_transactions_2024`, on any key, at any granularity.** This is not a "high-risk, use with caution" situation like the Day 1 NPCI bank-snapshot finding — it is an unconditional **INVALID**, because not even one dimension (identifier, time, currency, bank, merchant, or geography) offers a shared value space. **No enrichment of any `upi_transactions_2024` row with any field from this dataset is legitimate, and none is attempted anywhere in this audit or recommended anywhere below.**

---

## 7. Can It Provide Real Gateway / Routing Data for Aventum?

**Directly answering the brief's two example claims:**

> *"Gateway A experienced elevated failures."*

**Within this dataset's own population: yes, measurably.** `rail_id` shows a real, non-trivial failure-rate spread — 1.85% (`rail_A`, best) to 3.83% (`rail_C`, worst), a ~2.07× ratio — and this spread holds up consistently across every `payment_channel` within each rail (§12 cross-tab), confirming it is a genuine rail-level effect, not confounded by channel mix. This is categorically different from the Day 1 finding on `upi_transactions_2024`, where bank-level failure-rate spread (0.28–0.51 points) was statistically indistinguishable from noise. **This dataset genuinely demonstrates what "Gateway A experienced elevated failures" looks like, structurally** — but only as evidence about *this Nigerian card-payment population*, never as evidence about any UPI transaction (per §6, there is no link).

> *"Rerouting traffic from Gateway A to Gateway B would likely improve success."*

**The dataset supports the general shape of this claim (different rails have different steady-state success rates) but not a rigorous causal estimate of it**, because of the grain limitation established in §2: no transaction is ever observed on two rails, so there is no matched counterfactual pair to measure a true rerouting effect from. The most this dataset can support is a **between-subjects comparison** ("transactions historically sent to `rail_C` fail more often than transactions historically sent to `rail_A`"), which is suggestive but conflates rail assignment with any other unobserved confounder (this dataset shows no evidence of *why* transactions get assigned to a given rail — no routing-policy field exists per §2/§5, so we cannot rule out that some rails simply receive systematically different traffic).

**What it can instead reliably provide (all confirmed with real measurements from this dataset):**

- **Realistic latency distributions** — yes, and outcome-dependent in a plausible way (§11).
- **Response-code distributions** — yes, a clean 6-value vocabulary with realistic relative frequencies (§10).
- **Error-code distributions** — yes, identical distribution to response codes (redundant encoding).
- **Gateway/rail failure behavior** — yes, both steady-state (§12) and episodic/spike behavior (§9).
- **Routing-behavior patterns** — only traffic-share patterns (§12), not decision-logic patterns (no policy field exists).
- **Simulation parameters** — yes, this is the dataset's strongest legitimate use: realistic parameter *values* (failure-rate baselines per rail, spike magnitude/duration, latency-by-outcome-type shape, a plausible timeout threshold) to seed Aventum's own synthetic infrastructure layer, disclosed as calibration, not fact.

---

## 8. Counterfactual Simulation Value

| Derivable? | Answer | Basis |
|---|---|---|
| Baseline success by route | **Yes** | Direct `groupby(rail_id).status` computation, real measured values (§12). |
| Failure probability by route | **Yes** | Same computation, inverse. |
| Latency distribution by route | **Yes, but weakly differentiated** | Per-rail latency p50 spread is only 3.18ms — routes barely differ in typical latency even though they differ meaningfully in failure rate (§7). |
| Error distribution by route | **Yes** | `groupby(rail_id, gateway_response)` is directly computable. |
| Alternative-route outcome estimates | **Only as a between-subjects estimate, not a matched counterfactual** | No transaction is observed on two rails (§2) — any "transaction X would have succeeded on rail B" statement is a modeled projection from rail B's aggregate behavior, never a measured fact about transaction X. |
| Route-dependent failure probabilities | **Yes** | Directly measured per rail (§12). |
| Tradeoffs between latency and success | **Yes, directionally** | Failed transactions (of any kind) show materially higher latency than successful ones (899–3,986ms vs. 500ms, §11) — a real, measured association, though this reflects failure *causing* observable delay (e.g., retries/timeouts within the recorded latency) at least as plausibly as delay *causing* failure; the data cannot distinguish the two directions. |
| Potential recovery from rerouting | **Only as a projected estimate under stated assumptions**, never as an observed fact | Same reasoning as the alternative-route question above. |

**Mandatory separation, reinforced from Day 1 and directly applicable here:**

### Observed dataset facts
- Per-rail failure rate, latency distribution, and response/error-code distribution, **as measured within this Nigerian card-payment dataset's own 2,026,891 rows.**
- The existence of isolated, rail-specific, hour-long failure-rate spikes within this dataset (§9) — a real, measured pattern *in this data*.

### Simulator assumptions
- That a *different* payment population (e.g., `upi_transactions_2024` or a future live UPI feed) would exhibit **similar relative dynamics** if routed across analogous synthetic gateways (rail-level failure differentiation, a timeout-latency relationship, isolated spike-shaped degradation) — this is an assumption borrowed from this dataset's shape, not a measured fact about UPI transactions.
- Any specific numeric parameter (e.g., "assume synthetic Gateway B has a 1.85% baseline failure rate like `rail_A`") is a **design choice**, informed by this dataset, not a transferred fact.

### Synthetic incident parameters
- Spike magnitude (baseline × ~8–15), duration (single-hour in this dataset), and isolation (one rail at a time) as measured in §9 are **usable as realistic *shape* parameters** for Aventum's own incident-injection layer — again, a template, not a transplant.

**The simulator must never present a `rail_id`-based finding from this dataset as if it were a UPI-transaction fact, and must never claim a specific transaction "would have succeeded" on a different route as an observed outcome rather than a modeled projection.**

---

## 9. Incident Modeling Value

| Incident type | Can this dataset parameterize it? | Evidence |
|---|---|---|
| Gateway degradation | **Yes — the strongest fit** | §7/§12: real, meaningful per-rail failure-rate baselines (1.85%–3.83%) to model steady-state differences between synthetic gateways. |
| Latency spike | **Yes** | §11: `Timeout` rows show a clean, distinct high-latency band (mean 3,986ms vs. 500ms baseline) with an apparent ~2,000ms threshold — a ready-made shape for a "latency spike" incident. |
| Timeout spike | **Yes** | Directly observable as a `gateway_response=Timeout` concentration; 1,601 timeout rows total, with the outcome/latency relationship in §11 giving a realistic magnitude to model. |
| Response-code spike | **Yes** | §12/§10: 5 distinct non-Approved response codes, each individually trackable over time. |
| Error-code spike | **Yes (identical to response-code spike — redundant encoding)** | Same underlying signal as above. |
| Route-specific degradation | **Yes — directly measured, not just plausible** | §9 computed finding: each of the 5 rails has 3–5 discrete hours (of 168 total) where **that rail alone** spikes to 12–26% failure while every other rail stays within its normal 1–4% band at the same hour (confirmed rail-isolated via `routing_spike_check.py`). This is the single most directly useful finding in the entire dataset for Aventum's incident-modeling purpose: it is a **measured example of exactly the incident shape Aventum needs to detect and diagnose** (concentrated, time-bounded, single-segment, non-contagious to other segments). |
| Payment-channel degradation | **No — not supported** | §7/§12: `payment_channel` shows essentially no failure-rate variance (2.62%–2.69%, a 0.07-point spread) — there is no channel-specific degradation pattern in this data to learn from. |

**Important caveat carried from §4/§9:** these spike windows are **not labeled as incidents by the source dataset** — they were discovered by this audit's own hourly aggregation. They should be treated as **a well-shaped natural (or generator-injected) example to study**, not as pre-packaged incident ground truth Aventum can import directly. Building Aventum's own incident layer should be *informed* by this shape (isolated single-segment spikes of ~8–15× baseline, single-hour duration, with a plausible latency co-movement for timeout-type failures) rather than replaying these specific windows as if they were labeled incidents.

---

## 10. Error-Code Analysis

**Unique codes and frequency (identical distribution to `gateway_response`, confirmed 1:1 in §4):**

| `error_code` | `gateway_response` (paired) | Count | % of all rows |
|---|---|---|---|
| `''` (empty string) | Approved | 1,972,633 | 97.32% |
| `insufficient_funds` | Insufficient Funds | 13,313 | 0.657% |
| `processing_error` | Processing Error | 13,236 | 0.653% |
| `issuer_declined` | Issuer Declined | 13,146 | 0.649% |
| `do_not_honor` | Do Not Honor | 12,962 | 0.640% |
| `timeout` | Timeout | 1,601 | 0.079% |

**Mapping code → observed meaning:** the dataset provides no in-file legend, documentation, or dataset card (§1) — every meaning below is inferred from the **label text itself and its behavior**, not documented by the source:

| Code | Inferred category | Confidence |
|---|---|---|
| `insufficient_funds` | Issuer failure (payer's account lacks funds) | High — label is unambiguous and is a standard card-network decline reason |
| `issuer_declined` | Issuer failure (generic issuer-side decline) | High — label is unambiguous |
| `do_not_honor` | Issuer failure (a standard ISO 8583-family decline reason meaning the issuing bank generically refused authorization) | Medium-high — "Do Not Honor" is a well-known real-world card-decline code, but this dataset does not confirm it follows the actual ISO 8583 standard rather than using the phrase loosely |
| `processing_error` | Gateway/technical failure (ambiguous — could be gateway-side or network-side) | Low-medium — genuinely ambiguous without documentation |
| `timeout` | Network/technical failure (request did not complete in time) | High — label is unambiguous, and is corroborated by the measured latency behavior (§11) |
| `''` (Approved) | N/A (success) | — |

**No code in this set is opaque** (unlike a numeric-only code table would be) — every value is a readable English phrase. However, **the category assignments above are this audit's inference, not the dataset's own documentation**, and must be presented to any downstream consumer with that caveat. **None of these codes were invented by this audit** — they are the dataset's actual values; only the issuer/gateway/network/timeout/validation/routing *categorization* is inferred.

**Usefulness for Aventum's RCA evidence:** **useful as a vocabulary/template, not as evidence about any real incident.** The 5-way failure taxonomy (funds/issuer/issuer-generic/technical/timeout) is considerably more granular than Day 1's only prior evidence (NPCI's 2-way Bank-Declined/Technical-Declined split) and is a legitimate improvement to the **vocabulary** Aventum's synthetic error-code generator should draw from — but it remains a synthetic dataset's vocabulary, not observed real Nigerian gateway telemetry, and (per §6) cannot be attached to any specific UPI transaction.

---

## 11. Latency Analysis

**Overall distribution (milliseconds, n=2,026,891):**

| Stat | Value |
|---|---|
| min | 50.0 |
| median (p50) | 503.7 |
| mean | 513.2 |
| p90 | 668.6 |
| p95 | 725.1 |
| p99 | 993.6 |
| max | 6,891.1 |

**By status:**

| Status | count | mean | std | min | p50 | max |
|---|---|---|---|---|---|---|
| success | 1,972,633 | 500.08 | 119.94 | 50.0 | 500.07 | 1,137.42 |
| failed | 54,258 | 989.94 | 593.52 | 200.0 | 909.69 | 6,891.14 |

**By `gateway_response` (the clearest view — six clean bands):**

| gateway_response | count | mean | std | min | max |
|---|---|---|---|---|---|
| Approved | 1,972,633 | 500.08 | 119.94 | 50.0 | 1,137.42 |
| Do Not Honor | 12,962 | 899.88 | 251.21 | 200.0 | 1,738.99 |
| Insufficient Funds | 13,313 | 900.83 | 249.02 | 200.0 | 1,787.17 |
| Issuer Declined | 13,146 | 895.35 | 248.95 | 200.0 | 1,785.72 |
| Processing Error | 13,236 | 899.31 | 249.40 | 200.0 | 1,824.02 |
| Timeout | 1,601 | 3,986.16 | 799.08 | **2,000.0** | 6,891.14 |

**By `rail_id`:** meaningfully differentiated in *failure rate* but only trivially in *latency* — p50 ranges from 502.44ms (`rail_A`) to 505.62ms (`rail_C`), a 3.18ms spread (§12 table).

**By payment_channel:** essentially undifferentiated (means 512.5–513.4ms across all 4 channels).

**By time period:** hourly mean latency ranges 506.1–556.6ms across the 7-day window (§9), moving together with the hourly failure-rate spikes (the highest-latency hours are the same hours as the highest-failure-rate hours — confirmed by inspection of the top-10 lists in `routing_deep_analysis_output.txt` §F, which are nearly identical rosters).

**Sufficiency for modeling:**

- **Normal latency:** yes — dense, clean, realistic-shaped baseline (500ms ± 120ms for successful transactions).
- **Degradation:** yes — the non-timeout failure bands (~900ms, ~1.8× baseline) give a plausible "moderate degradation" shape.
- **Latency spikes:** yes — the `Timeout` band (~3,986ms, ~8× baseline, floor at exactly 2,000ms) gives a plausible "severe degradation" shape.
- **Timeout-like behavior:** yes, directly — `Timeout` is an explicit labeled outcome with a coherent latency signature, not something that has to be inferred indirectly.

This is, along with the rail-level failure differentiation, **the dataset's other standout contribution**: a clean, internally consistent, three-tier latency model (normal / degraded / timeout) that Aventum's synthetic infrastructure layer can use directly as a design template.

---

## 12. Gateway / Routing Analysis

**Number of rails:** 5 (`rail_A` through `rail_E`).

**Traffic distribution:**

| Rail | Volume | % of total |
|---|---|---|
| rail_B | 563,382 | 27.8% |
| rail_A | 535,225 | 26.4% |
| rail_D | 424,304 | 20.9% |
| rail_C | 258,667 | 12.8% |
| rail_E | 245,313 | 12.1% |

**Success/failure rate per rail:**

| Rail | Failure rate | Success rate |
|---|---|---|
| rail_A | 1.85% | 98.15% |
| rail_D | 2.39% | 97.61% |
| rail_B | 2.85% | 97.15% |
| rail_E | 3.40% | 96.60% |
| rail_C | 3.83% | 96.17% |

**Latency per rail:** p50 502.4–505.6ms (a 3.18ms spread — not a meaningful differentiator, see §11).

**Response/error distribution per rail:** proportional to each rail's overall failure rate; no rail shows a skew toward one *particular* failure type versus another (the 5 failure categories occur in roughly the same relative proportions within every rail's failure population — not separately tabulated in full here as it adds no new signal beyond the per-rail failure-rate table above).

**Explicit or merely categorical routing?** **Categorical only.** `rail_id` is a flat attribute recorded on the outcome row; there is no routing-decision event, no policy identifier, no "candidate rails considered" field, and no explanation of why a given transaction landed on a given rail (§2/§5). It functions as an *outcome-side label*, not a *decision-side log*.

**Can multiple rails be compared for similar transaction types?** **Yes, directly** — the rail × payment_channel cross-tabulation (§12 of `routing_deep_analysis_output.txt`) shows all 5 rails handling all 4 payment channels, so like-for-like comparison (e.g., "card payments on `rail_A` vs. card payments on `rail_C`") is fully supported and confirms the rail effect holds within each channel, not just in aggregate.

**Sufficient for a counterfactual routing simulator?** **Sufficient to calibrate one, not sufficient to validate one causally.** It provides real, differentiated, per-rail baseline success/failure/latency rates and a real example of isolated rail-specific degradation — genuinely useful simulation-calibration inputs. It cannot support a rigorous causal "if we had rerouted, we would have recovered X transactions" claim for any specific transaction, because (§2) no transaction is ever observed on more than one rail, so the counterfactual is always a projection from a different population's aggregate behavior, never a matched comparison.
