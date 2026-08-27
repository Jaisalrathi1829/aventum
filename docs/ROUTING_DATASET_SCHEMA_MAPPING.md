# Routing Dataset Schema Mapping

Compares every field in the Nigerian Card Payment Dataset for Predictive Routing against [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md) as it stands today. This document does **not** redesign the canonical schema — it only classifies each field's relationship to it, per the instruction to identify rather than automatically merge. Full field-level evidence is in [ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md).

---

## Exact matches

**None.** No field in this dataset shares both the same name *and* the same semantic meaning as a canonical field. The closest candidate, `status`, is a conceptual match with cosmetic differences — classified below under "semantics differ," not here, because even that pairing requires a value-casing transformation and, more importantly, must never be unioned across populations (see the "different population" warning repeated throughout this document).

## Fields that extend the schema

These fields represent genuinely new information the canonical schema does not currently have a slot for, and are strong candidates to inform (not populate) the schema's synthetic `Infrastructure` and `Incident Metadata` groups:

| Routing dataset field | Canonical field it extends | How |
|---|---|---|
| `rail_id` | `gateway_id` (Infrastructure, currently synthetic-only, no design template) | The first evidence anywhere in the Aventum data corpus of what a *populated, working* gateway-identity field looks like: 5 realistic values, an uneven-but-plausible traffic split, and a measurable, non-trivial failure-rate spread (1.85%–3.83%) tied to the identity itself. Directly informs the *design* of the synthetic `gateway_id` — realistic cardinality (single-digit count of gateways), realistic traffic imbalance, realistic failure-rate spread magnitude. |
| `latency_ms` | `gateway_latency_ms` (Infrastructure, currently synthetic-only) | Supplies a concrete, internally consistent three-tier latency template (normal ≈500ms / degraded ≈900ms / timeout ≈3,986ms with a clean 2,000ms floor) that the synthetic `gateway_latency_ms` design can adopt as a starting distribution shape. |
| `gateway_response` | `gateway_response_code` (Infrastructure, currently synthetic-only) | Supplies a realistic 6-value vocabulary (`Approved` + 5 decline reasons) — richer and more usable as label text than anything in the Day 1 corpus (which only had NPCI's 2-way Bank-Declined/Technical-Declined split). |
| The rail-isolated hourly spike pattern (§9 of the audit) | `incident_type` / `affected_segment` (Incident Metadata, currently synthetic-only) | No incident field exists in this dataset, but the *measured shape* of its naturally-occurring degradation windows (single-rail, ~1-hour duration, 8–15× baseline magnitude, latency co-movement) is a concrete, evidence-based template for what Aventum's own `incident_type='gateway_degradation'` scenarios should look like in magnitude and duration. |

**Important constraint on all four rows above:** "extends the schema" here means **informs the design of a still-synthetic field**, not "supplies real values for a real field." None of `rail_id`, `latency_ms`, `gateway_response`, or the spike pattern can be copied into any UPI-transaction row (per the unconditional no-join finding in [ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md) §6) — they extend the schema's *design knowledge*, not its *populated data*.

## Fields that conflict with existing definitions

| Routing dataset field | Canonical field | Conflict |
|---|---|---|
| `transaction_id` (format `tx_<int>`, e.g. `tx_1`) | `transaction_id` (format `TXN0000000001`, source `upi_transactions_2024`) | **Same field name, incompatible ID scheme, disjoint populations.** If both datasets were ever loaded into one physical table without a population/source discriminator, `transaction_id` would silently stop being a reliable unique key across the union unless namespaced (e.g. prefixed by source dataset). Must never be treated as the same identifier space. |
| `timestamp` (minute precision, UTC, Jan 2025) | `timestamp` (second precision, assumed IST, calendar year 2024) | Same field name, **different precision, different timezone assumption, and zero calendar-date overlap** (confirmed in the audit, §6). A naive union would silently mix two different clocks and two different resolutions under one column name. |
| `amount` (Nigerian Naira, presumed major unit, 100–13,305) | `amount` (Indian Rupees, `amount (INR)` source column, 10–42,099) | Same field name, **different currency with no conversion basis stated anywhere**. Must never be aggregated or compared numerically across the two without an explicit, disclosed FX assumption — and no such comparison is needed for any current Aventum requirement. |

## Fields whose semantics differ

These are the "attractive name, different concept" traps the task specifically warned against — each was checked against its actual values, not assumed compatible from the name:

| Routing dataset field | Superficially resembles | Why it's actually different |
|---|---|---|
| `payment_channel` (card / mobile_money / ussd / transfer) | `payment_method` (P2P / P2M / Bill Payment / Recharge) | `payment_channel` describes the **settlement instrument**; canonical `payment_method` describes the **UPI transaction's purpose**. A card payment can be for any purpose, and a P2P UPI transfer isn't tied to a specific instrument in the same sense — these are different axes entirely, not synonyms. Mapping one onto the other would fabricate a relationship that does not exist conceptually, independent of the join question. |
| `merchant_segment` (MID / ENT / SME) | `merchant_category` (Grocery / Food / Shopping / …) | `merchant_segment` is a **business-size classification**; canonical `merchant_category` is a **product/vertical classification**. An enterprise-segment merchant and a grocery-category merchant are answers to two unrelated questions. |
| `region` (constant `NG`) | `region` (10 named Indian states, sender-side) | Same field *name*, but the routing dataset's `region` is a degenerate country-code constant carrying zero variation (confirmed in the audit — every one of 2,026,891 rows is `NG`), while canonical `region` is a real multi-valued geographic dimension. This is the most dangerous pairing in this document precisely *because* the names match exactly while the content does not — a naive schema union on column name alone would silently overwrite a meaningful field with a meaningless one, or vice versa. |
| `status` (`success` / `failed`, lowercase) | `status` (`SUCCESS` / `FAILED`, uppercase, source `upi_transactions_2024`) | Conceptually the closest pairing in this whole document (both are a binary transaction-outcome flag), but still requires an explicit casing transformation and — as with every field above — must never be unioned across the two disjoint transaction populations. Listed here rather than under "extends the schema" because it doesn't add anything new; it only reinforces a concept the schema already has. |

## Fields that should remain dataset-specific

Fields with no current or foreseeable role in the shared canonical schema — useful only if this dataset itself is ever loaded as its own reference/demo population, never generalized:

- `reference` — fully redundant with `transaction_id` within this dataset (§1 of the audit); adds nothing.
- `merchant_id` — 300 anonymized Nigerian merchant codes, meaningful only within this dataset's own population; no counterpart concept exists in `upi_transactions_2024` (which has no merchant-identity field at all, only `merchant_category`).
- `merchant_segment` — dataset-specific business classification with an inferred (unconfirmed) meaning; not extended to the shared schema per the "semantics differ" finding above.
- `country` — constant `NG`, zero information; not worth carrying anywhere.

## Fields that can become simulation/calibration parameters

This is the routing dataset's actual point of contribution to Aventum, consolidated from [ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md) §7/§8/§9/§11/§12:

| Parameter | Value derived from this dataset | Feeds |
|---|---|---|
| Per-gateway baseline failure-rate spread | ~1.85%–3.83% across 5 rails (a genuine ~2× ratio, not noise — contrast with the Day 1 finding that `upi_transactions_2024`'s per-bank spread was noise-level at 0.28–0.51 points) | Synthetic `gateway_id` design — realistic magnitude for how much gateways should differ at steady state |
| Latency-by-outcome-type shape | 3-tier: success ≈500ms±120, non-timeout failure ≈900ms±250 (~1.8×), timeout ≈3,986ms±799 with a ~2,000ms floor (~8×) | Synthetic `gateway_latency_ms` design |
| Incident spike shape | Isolated to one rail at a time, ~1-hour duration, 8–15× baseline magnitude, latency co-moves with failure rate | Synthetic incident-injection layer's magnitude/duration/isolation parameters |
| Failure-reason vocabulary and relative frequency | 5-way split (funds/issuer-generic/issuer/technical/timeout), each 0.64–0.66% of total except timeout at 0.079% | Synthetic `gateway_response_code` vocabulary — richer than the Day 1 NPCI-only 2-way (BD%/TD%) reference |

**None of these four parameters are inserted into the canonical schema as data.** They are documented here as calibration inputs a future Day 2+ design session can choose to adopt when parameterizing the synthetic infrastructure layer — consistent with [ROUTING_DATASET_DECISION.md](ROUTING_DATASET_DECISION.md)'s classification of this dataset as calibration/reference material, not integrated data.
