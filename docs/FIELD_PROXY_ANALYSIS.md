# Field Proxy Analysis

For every field Aventum's architecture needs (see `Infrastructure` and `Banking/Issuer` groups in the CLAUDE.md project brief) that is not directly present, this document searches all 14 profiled datasets for a legitimate proxy and classifies it. Classifications: **EXACT FIELD**, **STRONG PROXY**, **WEAK PROXY**, **INVALID SUBSTITUTE / NO PROXY**.

---

## Banking / Issuer

| Required field | Candidate source | Classification | Reasoning |
|---|---|---|---|
| `issuer_bank` (bank authorizing/debiting the payer) | `upi_transactions_2024.sender_bank` | **EXACT FIELD** | In UPI terminology the payer's bank *is* the issuer/remitter. NPCI's own files use "Remitter Bank" for exactly this role (`npci_upi_remitter_banks.csv`), confirming the concept maps directly — this only needs a rename to canonical vocabulary, not a transformation. |
| `receiver_bank` / beneficiary bank | `upi_transactions_2024.receiver_bank` | **EXACT FIELD** | Matches NPCI's "Beneficiary Bank" concept directly. |
| Realistic per-bank decline-rate benchmark | `npci_upi_remitter_banks.csv` (`BD%`, `TD%`), `npci_upi_beneficiary_bank.csv` | **STRONG PROXY (for calibration only, not per-row)** | Real, dated (Sep-2023), cross-sectional bank-level decline rates including a genuine extreme outlier (Central Bank of India, 53.47% TD%). Valid as a **parameter reference** for how large a real bank-specific degradation can plausibly get when designing the synthetic incident layer. **Not valid** as a per-transaction join value — see [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) §2 for why row-level use would misrepresent both datasets' temporal scope. |

## Payment Context

| Required field | Candidate source | Classification | Reasoning |
|---|---|---|---|
| `payment_method` / transaction category | `upi_transactions_2024."transaction type"` (P2P/P2M/Bill Payment/Recharge) | **EXACT FIELD** | Directly observed, 4 clean categories, 0 nulls. |
| `merchant_category` | `upi_transactions_2024.merchant_category` | **STRONG PROXY, with a caveat** | Directly present and populated for every row — **but it is populated even for `transaction type = P2P` rows** (112,445 of 250,000), which in real UPI has no merchant at all (person-to-person transfer). Confirmed by crosstab (`audit_scripts`). This is a synthetic-generation artifact: treat `merchant_category` as meaningful only for `P2M`/`Bill Payment`/`Recharge` rows; for `P2P` rows it should be treated as **not applicable**, not as a real signal, despite being populated. |
| `region` / geography | `upi_transactions_2024.sender_state` | **STRONG PROXY (partial)** | Real Indian state names, but only 10 of 28+ states/UTs are represented, and only the **sender's** state — there is no receiver-side geography, no city, no pincode. Adequate as a coarse geographic dimension; insufficient for city-level or receiver-side geographic root-cause analysis. |
| `device` | `upi_transactions_2024.device_type` | **EXACT FIELD** | Directly observed (Android/iOS/Web), 0 nulls. |
| `network` | `upi_transactions_2024.network_type` | **EXACT FIELD** | Directly observed (4G/5G/WiFi/3G), 0 nulls. |

## Infrastructure (the largest gap — Aventum's core differentiator)

| Required field | Candidate source | Classification | Reasoning |
|---|---|---|---|
| `gateway` (payment gateway/switch handling the transaction) | none found in any dataset | **INVALID SUBSTITUTE / NO PROXY** | No dataset contains any gateway, PSP-routing, or switch identifier at the transaction level. `npci_upi_apps_RAW` has PSP **app** names (Amazon Pay, BHIM, etc.) at a *snapshot aggregate* level only — not joinable to individual transactions (no shared key, see [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) §6) and conceptually different (an "app" is not a "gateway"). **Conceptual note:** real UPI is architecturally a **single national switch (NPCI)**, not a multi-gateway network like card processing — so Aventum's "alternate gateway routing" framing must be introduced *as an explicit synthetic modeling choice* (e.g., representing competing PSP/bank endpoints as if they were gateways), and this framing should be disclosed as such, not presented as reflecting real UPI infrastructure. |
| `routing_path`, `routing_policy` | none found | **INVALID SUBSTITUTE / NO PROXY** | No routing information of any kind exists in any file. Must be fully synthetic. |
| `gateway_latency` / processing time | none found | **INVALID SUBSTITUTE / NO PROXY** | No timing field beyond the transaction's own `timestamp` exists anywhere in the corpus (no `processing_time`, `response_time`, `duration`, or similar in any of the 14 datasets — confirmed by column-name inspection of every file). Must be fully synthetic. |
| `gateway_response` (raw gateway/bank response code) | `upi_transactions_2024.transaction_status` (SUCCESS/FAILED only) | **WEAK PROXY** | Gives a binary outcome, not a response/error code. Cannot distinguish *why* a transaction failed. |
| `error_code` (granular technical failure reason) | `npci_upi_remitter_banks.csv` / `npci_upi_beneficiary_bank.csv` / `npci_upi_payers_performance_psp.csv` `BD%` (Bank Declined) vs `TD%` (Technical Declined) | **STRONG PROXY for a 2-category taxonomy; WEAK/INVALID as a per-transaction field** | This is a genuinely useful find: real NPCI reference data already distinguishes *bank-side* declines from *technical/infrastructure* declines — a legitimate seed for Aventum's root-cause category schema (`bank_decline` vs `technical_decline` as the top-level split). But it exists only as an **aggregate percentage per bank per snapshot month**, never as a **label on an individual transaction**. `upi_transactions_2024` has no per-row failure-reason field at all — every `FAILED` row is failed for an unstated reason. Assigning a synthetic per-transaction `error_code`/`decline_category` therefore cannot be avoided; the NPCI BD%/TD% split should inform the **realistic proportions** used when generating those synthetic labels (i.e., what fraction of synthetic failures should plausibly be bank-side vs technical), not supply the labels directly. |
| `gateway_health` (ongoing health/status signal) | none found | **INVALID SUBSTITUTE / NO PROXY** | No time-series health/uptime/status field exists for any entity at sub-monthly resolution. Must be fully synthetic. |

## Derived Analytics (all computable from `upi_transactions_2024`, not proxies — listed for completeness)

| Required field | Source | Classification |
|---|---|---|
| `rolling_success_rate`, `failure_rate`, `volume`, `gmv`, `error_rate` | computed from `upi_transactions_2024.transaction_status` / `amount (INR)` | **SUPPORTED THROUGH DERIVATION** (not a proxy question — see [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md)) |
| `latency_metrics`, `anomaly_score` on latency | none — depends on `gateway_latency`, which has no proxy | **NOT COMPUTABLE** until synthetic latency is introduced |

## Incident Metadata

| Required field | Candidate source | Classification | Reasoning |
|---|---|---|---|
| `incident_id`, `incident_start`, `incident_end`, `incident_type`, `affected_segment` | none | **INVALID SUBSTITUTE / NO PROXY** | No dataset marks any time window as an "incident" — see Ground-Truth Feasibility in [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md). Must be fully synthetic (introduced by Aventum's own incident-injection layer, then treated as ground truth **for evaluating Aventum**, never presented as an observed historical fact). |
| `ground_truth_root_cause` | `npci_upi_remitter_banks.csv` Central Bank of India 53.47% TD% outlier | **WEAK PROXY (existence proof only)** | Confirms that real, extreme, bank-specific technical-decline events do occur in the real ecosystem (useful to justify that Aventum's incident scenarios are realistic in *kind* and *magnitude*) but provides no timestamp, no onset/resolution boundary, and no causal narrative — it is a single cross-sectional data point, not an incident record. Cannot serve as an actual ground-truth label for any transaction. |

---

## Summary of proxy quality

| Category | Count | Fields |
|---|---|---|
| EXACT FIELD | 5 | issuer_bank(=sender_bank), receiver_bank, payment_method(=transaction type), device, network |
| STRONG PROXY | 4 | merchant_category (P2M/Bill/Recharge only), region (sender-side, 10-state), bank decline-rate benchmark (calibration only), error-code taxonomy shape (BD/TD split, calibration only) |
| WEAK PROXY | 2 | gateway_response(=transaction_status binary), ground_truth_root_cause (existence proof only) |
| INVALID SUBSTITUTE / NO PROXY | 6 | gateway, routing_path, routing_policy, gateway_latency, gateway_health, incident_id/start/end/type/affected_segment |

**Six required infrastructure/incident fields have no proxy anywhere in the corpus and must come entirely from the synthetic infrastructure layer** described in the project brief. This is expected and by design — the project brief explicitly anticipates this gap — but it must be sized correctly: it is not "a few missing columns," it is the entire gateway/routing/latency/health/incident dimension of the system. See [DATASET_ACQUISITION_PLAN.md](DATASET_ACQUISITION_PLAN.md) for the DOWNLOAD vs SYNTHESIZE decision on each.
