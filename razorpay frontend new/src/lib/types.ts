// ============================================================
// Aventum — frontend domain model.
//
// These types mirror the BACKEND's entities and use the backend's own names
// (incident_id, analysis_run_id, simulation_id, ...). That is deliberate: a second
// vocabulary in React would be a second place for a concept to drift, and §6 forbids it.
//
// Nothing here is a business rule. These are wire shapes; every value they carry was
// decided by a deterministic service and persisted before the browser saw it.
// ============================================================

// --- Provenance -------------------------------------------------------
// The backend emits AI_GENERATED; the design system keys the same concept as AGENT
// (its rendered label is already "AI-GENERATED"). The api layer maps between them so
// neither the palette nor the §9 vocabulary has to bend.
export type Truth =
  | "OBSERVED"
  | "SYNTHETIC"
  | "SIMULATED"
  | "PROJECTED"
  | "VERIFIED"
  | "DETERMINISTIC"
  | "HUMAN"
  | "AGENT";

export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "NONE";

export const UNAVAILABLE = "UNAVAILABLE";

/** A number the backend could supply, or the explicit absence of one. Never a zero. */
export type Maybe<T> = T | null;

export type EnvironmentNotice = {
  mode: string;
  detail: string;
  no_live_telemetry: boolean;
  no_production_execution: boolean;
  capacity: string;
};

// --- Health -----------------------------------------------------------
export type Health = {
  api: { ok: boolean; version: string };
  database: { ok: boolean; detail: string };
  agent: { ok: boolean; detail: string; required: boolean };
  environment: EnvironmentNotice;
};

// --- Incident ---------------------------------------------------------
export type IncidentSummary = {
  incident_id: number;
  incident_name: string;
  incident_type: string;
  status: string;
  affected_gateway_id: Maybe<string>;
  window_start: Maybe<string>;
  window_end: Maybe<string>;
  analysis_run_id: Maybe<number>;
  severity: Maybe<string>;
  confidence: Maybe<number>;
  significance_sigma: Maybe<number>;
  evidence_strength: Maybe<number>;
  verdict: Maybe<string>;
  predicted_root_cause: Maybe<string>;
  truth: Truth;
};

export type IncidentView = {
  incident_id: number;
  incident_name: string;
  incident_type: string;
  affected_gateway: Maybe<string>;
  affected_segment: Maybe<Record<string, unknown>>;
  start: string;
  end: string;
  severity: Maybe<number>;
  status: string;
  provenance: string;
};

export type DetectionView = {
  anomaly_id: number;
  /** PRIMARY alerts are actionable causes; DERIVATIVE ones are causal shadows (P1-1). */
  alert_role: "PRIMARY" | "DERIVATIVE";
  primary_anomaly_id: Maybe<number>;
  derived_from_anomaly_id: Maybe<number>;
  independence: Maybe<number>;
  severity: string;
  anomaly_score: number;
  significance_sigma: number;
  cohort_key: string;
  affected_population: number;
  baseline_metrics: Record<string, number>;
  current_metrics: Record<string, number>;
  detection_window: Record<string, unknown>;
  gmv_at_risk: number;
  rank: number;
};

export type EvidenceView = {
  evidence_id: number;
  evidence_type: string;
  metric: string;
  baseline: Maybe<number>;
  current: Maybe<number>;
  delta: Maybe<number>;
  significance_sigma: Maybe<number>;
  cohort: Record<string, unknown>;
  control: Maybe<Record<string, unknown>>;
  source_layer: string;
  evidence_source: string;
  explanation: string;
};

export type RcaView = {
  incident_id: Maybe<number>;
  analysis_run_id: number;
  verdict: string;
  predicted_root_cause: Maybe<string>;
  predicted_hypothesis_type: Maybe<string>;
  predicted_gateway_id: Maybe<string>;
  confidence: number;
  severity: string;
  significance_sigma: number;
  /** Carried separately from confidence — a gate may require both (Day 3 P1-2). */
  evidence_strength: number;
  summary: string;
  explanation: string;
  supporting_evidence_ids: number[];
  contradicting_evidence_ids: number[];
  alternatives_considered: Record<string, unknown>[];
  affected_population: Record<string, unknown>;
  control_population: Record<string, unknown>;
  rca_fingerprint: string;
};

export type GatewayHealth = {
  gateway_id: string;
  baseline_health_state: Maybe<string>;
  effective_failure_probability: Maybe<number>;
  effective_success_probability: Maybe<number>;
  baseline_failure_probability: Maybe<number>;
  baseline_traffic_weight: Maybe<number>;
  is_affected: boolean;
  incident_failure_multiplier: Maybe<number>;
  capacity: string;
  truth: Truth;
};

// --- Recovery lifecycle ------------------------------------------------
/** Derived from persisted rows on every request — never a stored status column. */
export type RecoveryState = {
  state:
    | "NO_ACTIVE_ACTION"
    | "NO_ACTION"
    | "POLICY_BLOCKED"
    | "AWAITING_APPROVAL_REQUEST"
    | "AWAITING_APPROVAL"
    | "APPROVAL_REJECTED"
    | "APPROVAL_EXPIRED"
    | "APPROVED"
    | "EXECUTION_REJECTED"
    | "VERIFYING"
    | "VERIFIED"
    | string;
  detail: string;
  outcome?: string;
  recommendation_id?: number;
  approval_id?: number;
  action_id?: number;
};

export type IncidentDetail = {
  environment: EnvironmentNotice;
  analysis_run_id: number;
  incident: Maybe<IncidentView>;
  detections: DetectionView[];
  derivative_detections: DetectionView[];
  evidence: EvidenceView[];
  rca: Maybe<RcaView>;
  gateway_health: GatewayHealth[];
  affected_gateway_id: Maybe<string>;
  recovery: RecoveryState;
};

// --- Simulation --------------------------------------------------------
export type Simulation = {
  simulation_id: number;
  candidate_key: string;
  action_type: string;
  source_gateway_id: Maybe<string>;
  target_gateway_id: Maybe<string>;
  traffic_percentage: Maybe<number>;
  status: string;
  invalid_reason: Maybe<string>;
  projected: {
    truth: Truth;
    baseline_success_rate: Maybe<number>;
    projected_success_rate: Maybe<number>;
    expected_success_delta: Maybe<number>;
    projected_failure_count: Maybe<number>;
    projected_gmv_total: Maybe<number>;
    projected_gmv_retained: Maybe<number>;
    projected_gmv_at_risk: Maybe<number>;
    projected_latency_p50: Maybe<number>;
    projected_latency_p95: Maybe<number>;
    latency_delta_ms: Maybe<number>;
    concentration_after: Maybe<number>;
    risk_score: Maybe<number>;
  };
  affected_population: Maybe<number>;
  rerouted_population: Maybe<number>;
  capacity_utilization: number | string;
  eligibility_result: Maybe<Record<string, unknown>>;
  risk_components: Maybe<Record<string, unknown>>;
  identity: {
    simulation_seed: Maybe<string>;
    input_fingerprint: Maybe<string>;
    simulation_fingerprint: Maybe<string>;
    model_version: Maybe<string>;
    policy_version: Maybe<string>;
    profile_version: Maybe<string>;
  };
  held_constant: Maybe<Record<string, unknown>>;
  changed_variables: Maybe<Record<string, unknown>>;
  assumptions: Maybe<Record<string, unknown>>;
  limitations: Maybe<Record<string, unknown>>;
  is_simulated: boolean;
  truth: Truth;
};

// --- Recommendation ----------------------------------------------------
export type Recommendation = {
  recommendation_id: number;
  incident_id: number;
  analysis_run_id: number;
  simulation_id: number;
  agent_run_id: Maybe<number>;
  action_type: string;
  source_gateway_id: Maybe<string>;
  target_gateway_id: Maybe<string>;
  traffic_percentage: Maybe<number>;
  expected: {
    truth: Truth;
    expected_success_delta: Maybe<number>;
    expected_gmv_retained: Maybe<number>;
    expected_latency_delta_ms: Maybe<number>;
  };
  risk: { truth: Truth; risk_score: Maybe<number>; risk_components: Maybe<Record<string, unknown>> };
  diagnosis: {
    truth: Truth;
    confidence: Maybe<number>;
    evidence_strength: Maybe<number>;
    significance_sigma: Maybe<number>;
    severity: Maybe<string>;
  };
  supporting_evidence_ids: number[];
  alternatives_considered: Maybe<Record<string, unknown>[]>;
  /** Agent prose. Never load-bearing for authorisation. */
  rationale: Maybe<string>;
  rationale_truth: Maybe<Truth>;
  /** The deterministic verdict. AI proposes; this decides. */
  policy: {
    truth: Truth;
    validation_result: string;
    reason_codes: Maybe<Record<string, unknown>>;
    constraints: Maybe<Record<string, unknown>>;
    policy_version: string;
  };
  status: string;
  expires_at: string;
  recommendation_fingerprint: string;
  model_version: string;
  created_at: string;
};

export type Staleness = {
  is_stale: boolean;
  reasons: string[];
  next_step: Maybe<string>;
};

// --- Approval / Action / Verification ----------------------------------
export type Approval = {
  approval_id: number;
  recommendation_id: number;
  status: "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED" | string;
  truth: Truth;
  requested_at: string;
  decided_at: Maybe<string>;
  expires_at: string;
  approver_identity: Maybe<string>;
  decision_note: Maybe<string>;
  approval_fingerprint: string;
  payload: Maybe<Record<string, any>>;
};

export type Action = {
  action_id: number;
  recommendation_id: number;
  approval_id: number;
  adapter_name: string;
  status: "EXECUTED" | "REJECTED" | "ROLLED_BACK" | string;
  rejection_reason: Maybe<string>;
  revalidation_result: Maybe<Record<string, unknown>>;
  pre_action_metrics: Maybe<Record<string, any>>;
  /** What was PREDICTED. Never merged with the measurement below. */
  expected_outcome: Maybe<Record<string, any>>;
  /** What was MEASURED after the simulated action. */
  actual_simulated_outcome: Maybe<Record<string, any>>;
  cohort_definition: Maybe<Record<string, unknown>>;
  measurement_window: Maybe<Record<string, unknown>>;
  execution_fingerprint: Maybe<string>;
  reference_simulation_fingerprint: Maybe<string>;
  executed_at: Maybe<string>;
  executed_by: Maybe<string>;
  is_simulated: boolean;
  created_at: string;
};

export type IntegrityCheck = { name: string; passed: boolean; detail: string };

export type Verification = {
  verification_id: Maybe<number>;
  action_id: number;
  status: "COMPLETE" | "INELIGIBLE" | string;
  /** RECOVERY_NOT_VERIFIED is a real, reachable answer — not an absence. */
  outcome: Maybe<"RECOVERY_EFFECTIVE" | "PARTIALLY_EFFECTIVE" | "RECOVERY_NOT_VERIFIED">;
  ineligible_reason: Maybe<string>;
  truth: Truth;
  baseline: { truth: Truth; failure_rate: Maybe<number>; success_rate: Maybe<number>; gmv_at_risk: Maybe<number> };
  projected: { truth: Truth; success_delta: Maybe<number>; gmv_retained: Maybe<number> };
  actual_simulated: { truth: Truth; failure_rate: Maybe<number>; success_rate: Maybe<number>; gmv_at_risk: Maybe<number> };
  measured: {
    truth: Truth;
    success_delta: Maybe<number>;
    failure_rate_improvement: Maybe<number>;
    gmv_recovered: Maybe<number>;
    variance_vs_projection: Maybe<number>;
    attainment_ratio: Maybe<number>;
    transactions_moved: Maybe<number>;
    population: Maybe<number>;
  };
  integrity_passed: boolean;
  integrity_checks: IntegrityCheck[];
  reasons: string[];
  verification_fingerprint: Maybe<string>;
};

export type RecommendationBundle = {
  environment: EnvironmentNotice;
  incident_id: number;
  recommendation: Maybe<Recommendation>;
  stale: Maybe<Staleness>;
  approval: Maybe<Approval>;
  action: Maybe<Action>;
  verification: Maybe<Verification>;
  recovery: RecoveryState;
};

// --- Audit ------------------------------------------------------------
export type AuditEvent = {
  event_id: number;
  incident_id: Maybe<number>;
  event_type: string;
  actor: string;
  input_ref: Maybe<Record<string, unknown>>;
  output_ref: Maybe<Record<string, unknown>>;
  payload: Maybe<Record<string, unknown>>;
  model_version: Maybe<string>;
  policy_version: Maybe<string>;
  tool_version: Maybe<string>;
  fingerprint: Maybe<string>;
  occurred_at: string;
};

// --- Agent ------------------------------------------------------------
export type AgentToolCall = {
  tool_call_id: number;
  sequence: number;
  tool_name: string;
  outcome: Maybe<string>;
  attempt: Maybe<number>;
  latency_ms: Maybe<number>;
  created_at: Maybe<string>;
};

export type AgentRun = {
  agent_run_id: number;
  incident_id: Maybe<number>;
  analysis_run_id: Maybe<number>;
  status: string;
  truth: Truth;
  model_name: Maybe<string>;
  turns_used: Maybe<number>;
  tool_calls_used: Maybe<number>;
  simulations_used: Maybe<number>;
  context_tokens_max: Maybe<number>;
  started_at: Maybe<string>;
  finished_at: Maybe<string>;
  error_message: Maybe<string>;
  tool_calls: AgentToolCall[];
};

// --- Batch ------------------------------------------------------------
export type BatchRecovery = {
  incidents_evaluated: number;
  interventions_proposed: number;
  no_action_count: number;
  policy_blocked_count: number;
  approvals_requested: number;
  approvals_granted: number;
  approvals_rejected: number;
  approvals_expired: number;
  interventions_executed: number;
  executions_rejected: number;
  interventions_verified: number;
  recovery_effective_count: number;
  partially_effective_count: number;
  recovery_not_verified_count: number;
  /** A projection over recommendations. Never summed with the measurement below. */
  total_projected_gmv_retained: number;
  /** A measurement over verified actions. */
  total_actual_gmv_recovered: number;
  recovery_uplift: number | string;
  verification_success_rate: number | string;
  intervention_rate: number | string;
  no_action_rate: number | string;
  transactions_moved: number;
  provenance: string;
  recovery_claim: string;
  notes: Record<string, string>;
};

export type Overview = {
  environment: EnvironmentNotice;
  incidents: IncidentSummary[];
  active_incident_count: number;
  primary_incident: Maybe<IncidentSummary>;
  recovery: RecoveryState;
  batch: BatchRecovery;
};
