// ============================================================
// Presentation mapping for the backend's recovery state.
//
// This file decides NOTHING. The backend derives `RecoveryState.state` from persisted
// rows on every request; everything here turns that one string into a label, a colour
// and a set of completed workflow steps.
//
// Keeping the mapping in one place is what stops the tone drifting between the sidebar,
// the stepper and the incident header — and, more importantly, stops any component
// inventing a state the backend never reported.
// ============================================================

import type { RecoveryState } from "./types";

type Tone = "neutral" | "accent" | "success" | "warning" | "critical" | "muted";

/** Terminal states where the system deliberately stopped rather than failed (§20). */
const STOPPED_STATES = new Set([
  "NO_ACTION",
  "POLICY_BLOCKED",
  "APPROVAL_REJECTED",
  "APPROVAL_EXPIRED",
  "EXECUTION_REJECTED",
]);

export function isStopped(state: string): boolean {
  return STOPPED_STATES.has(state);
}

export function recoveryTone(state: string): Tone {
  switch (state) {
    case "VERIFIED":
      return "success";
    case "EXECUTION_REJECTED":
    case "APPROVAL_REJECTED":
    case "APPROVAL_EXPIRED":
      return "critical";
    case "POLICY_BLOCKED":
    case "AWAITING_APPROVAL":
    case "AWAITING_APPROVAL_REQUEST":
      return "warning";
    case "VERIFYING":
    case "APPROVED":
      return "accent";
    case "NO_ACTION":
      return "muted";
    default:
      return "muted";
  }
}

export function recoveryLabel(state: string): string {
  switch (state) {
    case "NO_ACTIVE_ACTION":
      return "No active action";
    case "NO_ACTION":
      return "No action required";
    case "POLICY_BLOCKED":
      return "Blocked by policy";
    case "AWAITING_APPROVAL_REQUEST":
      return "Ready for approval request";
    case "AWAITING_APPROVAL":
      return "Awaiting human approval";
    case "APPROVAL_REJECTED":
      return "Approval declined";
    case "APPROVAL_EXPIRED":
      return "Approval expired";
    case "APPROVED":
      return "Approved, not executed";
    case "EXECUTION_REJECTED":
      return "Execution rejected";
    case "VERIFYING":
      return "Executed, awaiting verification";
    case "VERIFIED":
      return "Verified";
    default:
      return state.replace(/_/g, " ");
  }
}

/**
 * The incident header's one-word status.
 *
 * Note EXECUTED and VERIFIED are separate words here, never collapsed — §49 requires
 * that distinction to survive all the way to the top of the screen.
 */
export function incidentStatusLabel(state: string): string {
  switch (state) {
    case "VERIFIED":
      return "VERIFIED";
    case "VERIFYING":
      return "EXECUTED";
    case "APPROVED":
      return "APPROVED";
    case "AWAITING_APPROVAL":
      return "AWAITING APPROVAL";
    case "NO_ACTION":
      return "NO ACTION";
    case "POLICY_BLOCKED":
      return "BLOCKED";
    case "APPROVAL_REJECTED":
    case "APPROVAL_EXPIRED":
    case "EXECUTION_REJECTED":
      return "STOPPED";
    default:
      return "INVESTIGATING";
  }
}

/**
 * Which authority-chain steps are complete, derived from backend state alone.
 *
 * The prototype tracked this with local booleans (`approved`, `executed`, `verified`).
 * Those are precisely the forbidden authoritative frontend flags (§2): a browser refresh
 * reset them, and a second operator saw a different workflow than the first. This
 * function replaces them, so the stepper is a rendering of the database.
 */
export type StepProgress = {
  diagnosed: boolean;
  simulated: boolean;
  recommended: boolean;
  policyValidated: boolean;
  approved: boolean;
  executed: boolean;
  verified: boolean;
};

export function stepProgress(recovery: RecoveryState | null | undefined): StepProgress {
  const state = recovery?.state ?? "NO_ACTIVE_ACTION";
  const hasRecommendation = recovery?.recommendation_id != null;
  const hasApproval = recovery?.approval_id != null;
  const hasAction = recovery?.action_id != null;

  const approved =
    state === "APPROVED" ||
    state === "VERIFYING" ||
    state === "VERIFIED" ||
    state === "EXECUTION_REJECTED";

  return {
    // A recommendation exists only after diagnosis and simulation produced it, so its
    // presence is sufficient evidence for both.
    diagnosed: true,
    simulated: hasRecommendation,
    recommended: hasRecommendation,
    // Policy runs on every recommendation, including the ones it blocks.
    policyValidated: hasRecommendation,
    approved: approved || (hasApproval && state !== "AWAITING_APPROVAL"),
    executed: state === "VERIFYING" || state === "VERIFIED" || (hasAction && state !== "EXECUTION_REJECTED"),
    verified: state === "VERIFIED",
  };
}
