_Aventum internal investigation — observed-content MD5 discrepancy._

# MD5 Integrity Resolution

**Verdict up front: the observed dataset did not change. Both hashes are correct digests of the same unchanged 250,000 rows, computed over different field sets. The defect is a naming collision in documentation, severity P2.**

Investigation only. No production code, database row, migration, or expected fingerprint was modified — the working tree was clean before and after, and every query issued was a `SELECT`.

---

## Problem

Two different values are documented as the "observed content MD5" of the same canonical UPI dataset, each described as unchanged before/after its operation:

| Value | Where |
|---|---|
| `13965d76407219517a57702df5f24226` | `DAY3_ARCHITECTURE_REVIEW.md`, `DAY3_P1_FIX_REPORT.md` |
| `2674c4d8d0452469687b8e19022efd19` | `DAY4A_IMPLEMENTATION_REPORT.md` |

A reader comparing the two reports would reasonably conclude the observed data mutated between Day 3 and Day 4A. It did not.

---

## Historical Hash #1 — `13965d76407219517a57702df5f24226`

**First appearance:** commit `a79bc92`, 2026-08-27, "Day 3 independent architecture review". Restated in `7923158` (Day 3 P1 fixes) as the pre-fix baseline. Appears in documentation only — no committed code produces this exact expression.

**Scope, as stated in the Day 3 review itself** (`DAY3_ARCHITECTURE_REVIEW.md` §1):

> "The content hash covers `transaction_id | status | amount | timestamp` for all 250,000 rows ordered by id"

**That prose is accurate.** Reproduced live:

```sql
SELECT md5(string_agg(
         transaction_id || '|' || status || '|' || amount::text || '|' || timestamp::text,
         ',' ORDER BY transaction_id))
FROM transactions;
-- 13965d76407219517a57702df5f24226   ✓ exact match
```

Four observed fields, `|` between fields, `,` between rows, explicit `ORDER BY transaction_id`.

---

## Historical Hash #2 — `2674c4d8d0452469687b8e19022efd19`

**First appearance:** commit `8dd85b4`, 2026-08-28, "Day 4A: deterministic decision core". Appears in `DAY4A_IMPLEMENTATION_REPORT.md` and, as executable code, in `backend/tests/test_decision_core.py` (the three prior-layer immutability regression tests).

**Scope** — reproduced live:

```sql
SELECT md5(string_agg(
         transaction_id || status || amount::text,
         '' ORDER BY transaction_id))
FROM transactions;
-- 2674c4d8d0452469687b8e19022efd19   ✓ exact match
```

**Three** observed fields, no separators, explicit `ORDER BY transaction_id`. **`timestamp` is absent.**

---

## Exact Hashing Methods

Three distinct methods exist in the project. All are order-deterministic (every one carries an explicit `ORDER BY transaction_id`; none relies on database natural order).

| Method | Fields | Field sep | Row sep | Ordering | Value | Where | Published as a constant? |
|---|---|---|---|---|---|---|---|
| **M1** | id, status, amount, **timestamp** | `\|` | `,` | `ORDER BY transaction_id` | `13965d76…f5f24226` | Day 3 review + P1 report | **Yes** |
| **M2** | id, status, amount | `\|` | `,` | `ORDER BY transaction_id` | `f2072bcd8d9fd681dc113a47ae31e981` | `test_incident_intelligence.py:549` (`62d4a20`) | No — relative before/after only |
| **M3** | id, status, amount | *(none)* | *(none)* | `ORDER BY transaction_id` | `2674c4d8…022efd19` | Day 4A report + `test_decision_core.py` | **Yes** |

M2 is worth noting but is not part of the discrepancy: it is used inside a fixture-scale test as a self-comparison (hash before, hash after, assert equal) and its value is never published or compared across phases. It has never run against the 250,000-row canonical table.

**The collision is M1 vs M3.** Both were published as a named project constant called "Observed content MD5", with no statement of scope in the Day 4A case.

### Trace table

| Hash | First appearance | Producer | Input | Scope | Algorithm | Ordering | Purpose |
|---|---|---|---|---|---|---|---|
| `13965d76…` | `a79bc92` 2026-08-27 | ad-hoc SQL in the Day 3 review session (doc-only) | live `transactions` | 4 fields incl. `timestamp` | MD5 over `string_agg` | explicit, by `transaction_id` | prove observed bytes unchanged across Day 3 review activity |
| `2674c4d8…` | `8dd85b4` 2026-08-28 | `test_decision_core.py` + Day 4A report | live `transactions` | 3 fields, no `timestamp` | MD5 over `string_agg` | explicit, by `transaction_id` | prove observed bytes unchanged across a Day 4A full flow |

---

## Freshly Computed Values

Recomputed from the current live source. Nothing below was read from a stored or documented value.

| Artifact | Value |
|---|---|
| **Raw file MD5** (`upi_transactions_2024.csv`, exact bytes) | `c58ae6208facf23617958cc6b3083376` |
| **Raw file SHA-256** | `8e46a45fd12c3e9e75a7cf1ac73604bdd9b2bd72859e3374d0153256ac4c89b6` ✓ matches canonical |
| Raw file size / lines | 29,811,789 bytes / 250,001 lines (250,000 rows + header) |
| **M1 database digest** | `13965d76407219517a57702df5f24226` ✓ matches Day 3 |
| **M3 database digest** | `2674c4d8d0452469687b8e19022efd19` ✓ matches Day 4A |
| Canonical fingerprint | `12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8` ✓ unchanged |
| Generation fingerprint | `e8414edd5a58c6cf04876e1bf48ca9a5564cf8d77da8eca4201c1732f52fe3c8` ✓ unchanged |

**Note the raw-file MD5 is a third distinct value** (`c58ae620…`) and matches neither documented hash. Neither published hash is a raw-file digest; both are database-derived. No documentation ever claimed otherwise, but recording it here removes the possibility.

---

## Byte / Serialization Differences

The first point of divergence between M1 and M3 is the **field set**, not any encoding, ordering, or normalization issue:

| Dimension | M1 | M3 | Divergent? |
|---|---|---|---|
| Field set | id, status, amount, **timestamp** | id, status, amount | **YES — the sole cause** |
| Field separator | `\|` | none | yes (secondary) |
| Row separator | `,` | none | yes (secondary) |
| Ordering | explicit `ORDER BY transaction_id` | explicit `ORDER BY transaction_id` | no |
| Encoding | UTF-8 (PostgreSQL server) | UTF-8 | no |
| `amount` rendering | `numeric(12,2)::text` → `868.00` | identical | no |
| `timestamp` rendering | `timestamptz::text` → `2024-06-15 07:00:45+00` | not included | n/a |
| NULL handling | no NULLs in these four columns | same | no |
| Line endings / trailing newline | none — `string_agg`, not a file | same | no |
| CSV parsing | not involved — both read the table | same | no |

Isolating the field set alone confirms it: the 4-field and 3-field variants under M1's *own* separator convention are `13965d76…` and `f2072bcd…` respectively. Adding `timestamp` is what moves the value.

**Ordering was explicitly checked and ruled out.** Both methods pin `ORDER BY transaction_id`. For completeness, the same expression with no `ORDER BY` (database natural order) yields `acbffafd05b0385df62bbedd5141aa8a`, and ordered by `timestamp` yields `e63f0c66c1b4117cae9723e1136bda90` — neither is a documented value, so neither published hash was ever computed from a non-deterministic ordering.

---

## Git History

| Commit | Date | Event |
|---|---|---|
| `62d4a20` | 2026-08-27 | Day 3 implementation. Introduces **M2** in `test_incident_intelligence.py` — fixture-scale, self-comparison, unpublished. |
| `a79bc92` | 2026-08-27 | Day 3 architecture review. Publishes **M1** = `13965d76…`, with its 4-field scope stated in prose. |
| `7923158` | 2026-08-27 | Day 3 P1 fixes. Restates M1 as the pre-fix baseline. Scope **not** restated. |
| `8dd85b4` | 2026-08-28 | Day 4A. Introduces **M3** in `test_decision_core.py` and publishes `2674c4d8…` under the same label as M1. |

One method did not replace another, and no hashing implementation was changed. M1 was never implemented in committed code, so Day 4A had no committed expression to inherit; a new one was written and given the existing name. History was not modified during this investigation.

---

## Dataset Immutability Verification

Established **without relying on either MD5**.

| Check | Result |
|---|---|
| Raw file SHA-256 | `8e46a45f…c89b6` — matches canonical exactly |
| Raw file row count | 250,000 (+ header) |
| `transactions` row count | 250,000 |
| Distinct `transaction_id` | 250,000 (no duplicates, no loss) |
| Observed FAILED / SUCCESS | 12,376 / 237,624 — FAILED matches the Day 3 review's documented 12,376 |
| `SUM(amount)` | 327,939,009.00 |
| MIN / MAX amount | 10.00 / 42,099.00 |
| MIN / MAX timestamp | 2023-12-31 18:35:10+00 / 2024-12-30 18:25:40+00 |
| Canonical fingerprint | `12dec963…f4b8` — unchanged |
| Registered `source_sha256` | `8e46a45f…c89b6` — unchanged |
| Generation fingerprint | `e8414edd…2fe3c8` — unchanged |

### Field-level raw-CSV ↔ database comparison

The raw CSV was re-parsed in Python and compared row by row against the loaded table:

| Comparison | Result |
|---|---|
| `transaction_id` sets identical | **True** |
| IDs only in CSV | **0** |
| IDs only in database | **0** |
| `amount` mismatches | **0 of 250,000** |
| `status` mismatches | **0 of 250,000** |

### Fully independent digest path

A digest computed in **Python, over the raw CSV bytes, outside PostgreSQL entirely** reproduces the database digest exactly once numeric scale is matched:

```
python, naive Decimal repr ("868")    : 8f30dab33ddaad7e2bb4e9c0c2bdf0e0
python, quantized to 2dp   ("868.00") : 2674c4d8d0452469687b8e19022efd19
postgres numeric(12,2)::text          : 2674c4d8d0452469687b8e19022efd19   ← identical
```

The initial Python/PostgreSQL mismatch was traced fully and is a rendering artifact only: `numeric(12,2)::text` always emits two decimal places, while `Decimal("868")` renders as `868`. Matching the scale closes the gap exactly. This is recorded rather than waved away because "a hash didn't match and I assumed it was formatting" is precisely the reasoning this investigation exists to reject.

**Conclusion: the observed dataset is byte-for-byte the dataset ingested on Day 2A. There is no mutation.**

---

## Final Classification

# A — SAME DATA, DIFFERENT HASHING SCOPE

Both hashes are valid, correct, reproducible digests of the same unchanged 250,000 rows. M1 covers four observed fields; M3 covers three. Neither is wrong as a digest.

**Compounded by a secondary documentation defect (category B):** the Day 4A report published M3 under the name "Observed content MD5" — the identical label Day 3 used for M1 — without stating its scope. The number is right; the label is ambiguous, and the ambiguity is what manufactured the appearance of a mutation.

Explicitly **not**:
- **not C (implementation error)** — no implementation changed; M1 was never in committed code, and M3 is correct for what it computes. Both use explicit deterministic ordering.
- **not D (data mutation)** — disproved four independent ways: raw SHA-256, canonical fingerprint, field-level CSV↔DB comparison with zero mismatches, and an out-of-database Python digest.
- **not E (unresolved)** — both values reproduce exactly from the live source.

---

## Canonical Interpretation

Going forward these are **two distinct named artifacts**, and the generic phrase "observed content MD5" is retired:

| Name | Definition | Value (current, and expected to remain) |
|---|---|---|
| `RAW_FILE_MD5` | MD5 of the exact bytes of `data/raw/UPI Transactions 2024 Dataset/upi_transactions_2024.csv` | `c58ae6208facf23617958cc6b3083376` |
| `RAW_FILE_SHA256` | SHA-256 of the same bytes — the registered dataset identity | `8e46a45f…c89b6` |
| `OBSERVED_CONTENT_MD5_V1` | `md5(string_agg(transaction_id‖'\|'‖status‖'\|'‖amount::text‖'\|'‖timestamp::text, ',' ORDER BY transaction_id))` over `transactions` | `13965d76407219517a57702df5f24226` |
| `OBSERVED_CONTENT_MD5_V2` | `md5(string_agg(transaction_id‖status‖amount::text, '' ORDER BY transaction_id))` over `transactions` | `2674c4d8d0452469687b8e19022efd19` |
| `CANONICAL_FINGERPRINT` | Day 2A load-time deterministic checksum (`aventum_ingest.integrity`) | `12dec963…f4b8` |

**`OBSERVED_CONTENT_MD5_V1` is the preferred immutability digest** for future phases, because its four-field scope is strictly stronger: it would additionally catch a `timestamp` mutation, which V2 cannot see. V2 remains valid and is retained as the value already committed in Day 4A's regression tests.

No value above may be edited to force agreement with another. They are digests of different things and are expected to differ.

---

## Required Documentation Correction

**Smallest sufficient change: two clarifying edits, no value altered anywhere.**

1. `docs/DAY4A_IMPLEMENTATION_REPORT.md` — relabel its row from "Observed content MD5" to "Observed content MD5 (V2: id+status+amount)" and note it is a different scope from Day 3's V1, not a changed value.
2. `docs/CURRENT_AVENTUM_HANDOFF.md` — record both named artifacts with their scopes, replacing the single ambiguous "Observed-content MD5 (drift check)" entry.

**Deliberately not corrected:** `DAY3_ARCHITECTURE_REVIEW.md` and `DAY3_P1_FIX_REPORT.md`. The Day 3 review states its scope correctly, and its value is correct. These are historical phase-gate records; amending them would rewrite an accurate report to accommodate a later naming slip. The Day 3 P1 report restates the value without scope, which is a minor inheritance of the same ambiguity — acceptable in a historical document that cites the review directly above it.

**These edits are recommended, not applied.** This task is investigation-only; the corrections belong to whoever picks up the follow-up, and are listed here so that decision is a deliberate one.

---

## Impact on Day 4A

**None. Day 4A remains fully valid.**

- The three Day 4A immutability regression tests compare M3 against itself, before and after a full flow. A self-comparison is correct regardless of which field set it covers — it proves the observed table did not change during the flow, which is exactly what it claims.
- No Day 4A behaviour, threshold, gate, fingerprint, or decision depends on either published MD5.
- Alembic head remains `0006`; the canonical and generation fingerprints are unchanged.
- The 472/472 test result stands. No test asserted a hardcoded `13965d76…`, so nothing was passing for the wrong reason.

The only Day 4A shortcoming is the report's label, which overstated continuity with Day 3 by reusing its name for a different measurement.

---

## Impact on Day 4B

**None. Day 4B is safe to start.**

Day 4B reads Day 3 evidence and Day 4A simulations through typed interfaces and does not consume either MD5. One instruction carries forward: any future report must name the digest it quotes (`OBSERVED_CONTENT_MD5_V1` / `_V2` / `RAW_FILE_MD5`) rather than writing "observed content MD5", so this ambiguity cannot recur.

---

## Final Integrity Verdict

# NO INTEGRITY DEFECT IN THE DATA — P2 DOCUMENTATION AMBIGUITY

**Severity P2.** No data, code, schema, or safety property is affected; the correction is two clarifying sentences. It is not P1 because nothing downstream consumes the value and no decision was made on it — but it is worth recording rather than dismissing, because a phase report that appears to show the immutable dataset mutating is exactly the kind of thing that erodes trust in every other number in the document.

| Acceptance criterion | Status |
|---|---|
| Both MD5 values fully explained | ✅ both reproduced exactly from live source |
| Raw CSV integrity independently verified | ✅ SHA-256 matches; 250,000 rows |
| Observed database data verified unchanged | ✅ 0 id/amount/status mismatches vs raw CSV |
| Canonical SHA-256 unchanged | ✅ `8e46a45f…c89b6` |
| Canonical fingerprint unchanged | ✅ `12dec963…f4b8` |
| Generation fingerprint unchanged | ✅ `e8414edd…2fe3c8` |
| No production code modified | ✅ working tree clean |
| No database data modified | ✅ SELECT-only throughout |
| No migration modified | ✅ head `0006` |
| No expected fingerprint changed to force a pass | ✅ none touched |
| One canonical interpretation documented | ✅ five named artifacts above |
| Future reports disambiguated | ✅ naming convention defined; two edits specified |
