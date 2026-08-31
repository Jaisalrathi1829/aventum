// ============================================================
// Aventum — the single door to the backend.
//
// Every request in the application goes through `request()`. Components never call
// fetch, never build a URL, and never see a status code (§7). That is what makes
// timeouts, error shapes and the base URL one decision each instead of thirty.
//
// The base URL comes from the environment, never a hard-coded localhost (§7/§36).
// ============================================================

import type {
  Action,
  AgentRun,
  Approval,
  AuditEvent,
  BatchRecovery,
  EnvironmentNotice,
  Health,
  IncidentDetail,
  Overview,
  Recommendation,
  RecommendationBundle,
  Simulation,
  Truth,
  Verification,
} from "./types";

const BASE_URL: string =
  (import.meta.env.VITE_AVENTUM_API_URL as string | undefined)?.replace(/\/$/, "") ??
  "http://localhost:8000";

/** Long enough for a real counterfactual sweep, short enough to fail visibly (§33-D). */
const DEFAULT_TIMEOUT_MS = 30_000;
/** The agent runs a multi-turn loop against a local 8B model; it needs its own budget. */
const AGENT_TIMEOUT_MS = 200_000;

/**
 * A failure the UI can branch on.
 *
 * `code` is the backend's stable identifier where one was supplied, or a transport-level
 * code we assign. Components render `message`; they never parse it.
 */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly detail?: unknown;

  constructor(code: string, message: string, status = 0, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.detail = detail;
  }

  /** True when retrying could plausibly succeed — nothing was decided server-side. */
  get retryable(): boolean {
    return (
      this.code === "NETWORK_UNREACHABLE" ||
      this.code === "TIMEOUT" ||
      this.status >= 500
    );
  }
}

type RequestOptions = {
  method?: "GET" | "POST";
  body?: unknown;
  timeoutMs?: number;
  signal?: AbortSignal;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, timeoutMs = DEFAULT_TIMEOUT_MS } = options;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  // Let a caller-supplied signal (component unmount) also cancel the request.
  options.signal?.addEventListener("abort", () => controller.abort());

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timer);
    if (controller.signal.aborted) {
      throw new ApiError("TIMEOUT", "The request timed out before the backend responded.");
    }
    // A dead backend must read as a backend problem, not as an empty dashboard (§33-A).
    throw new ApiError(
      "NETWORK_UNREACHABLE",
      "Cannot reach the Aventum backend. Check that the API is running.",
    );
  } finally {
    clearTimeout(timer);
  }

  const text = await response.text();
  let payload: any = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      // A 200 carrying HTML or a truncated body is a malformed response, not data (§33-C).
      throw new ApiError(
        "MALFORMED_RESPONSE",
        "The backend returned a response this application could not read.",
        response.status,
      );
    }
  }

  if (!response.ok) {
    // FastAPI wraps HTTPException payloads in `detail`; ours carry {code, message}.
    const envelope = payload?.detail ?? payload ?? {};
    throw new ApiError(
      envelope.code ?? "REQUEST_FAILED",
      envelope.message ?? "The request was refused by the backend.",
      response.status,
      envelope.detail,
    );
  }

  return payload as T;
}

/**
 * The backend says AI_GENERATED; the design system keys that concept as AGENT.
 * Mapped here, once, so no component has to know both spellings.
 */
export function toTruth(value: string | null | undefined): Truth | undefined {
  if (!value) return undefined;
  if (value === "AI_GENERATED") return "AGENT";
  return value as Truth;
}

/** Formats a backend value that may legitimately be absent. Never invents a zero. */
export function orUnavailable(
  value: number | string | null | undefined,
  format?: (n: number) => string,
): string {
  if (value === null || value === undefined) return "UNAVAILABLE";
  if (typeof value === "string") return value;
  return format ? format(value) : String(value);
}

// ============================================================
// Endpoints. One function per backend route, named for what it returns.
// ============================================================
export const api = {
  // 12s, not 6s. When the database is down, health legitimately costs ~4s to answer:
  // libpq's connect timeout floors at 2s and psycopg tries IPv6 then IPv4. A 6s budget
  // left almost no margin, so health calls intermittently aborted and the sidebar
  // reported the API as unreachable -- precisely when its report mattered most.
  health: () => request<Health>("/api/health", { timeoutMs: 12_000 }),

  overview: () => request<Overview>("/api/overview"),

  incident: (incidentId: number) => request<IncidentDetail>(`/api/incidents/${incidentId}`),

  simulations: (incidentId: number) =>
    request<{ environment: EnvironmentNotice; incident_id: number; simulations: Simulation[] }>(
      `/api/incidents/${incidentId}/simulations`,
    ),

  simulation: (simulationId: number) =>
    request<Simulation & { environment: EnvironmentNotice }>(`/api/simulations/${simulationId}`),

  /** Runs the DETERMINISTIC decision pipeline. Works with the agent offline. */
  analyze: (incidentId: number) =>
    request<{ recommendation: Recommendation; requires_approval: boolean; elapsed_ms: number }>(
      `/api/incidents/${incidentId}/analyze`,
      { method: "POST", timeoutMs: 60_000 },
    ),

  recommendation: (incidentId: number) =>
    request<RecommendationBundle>(`/api/incidents/${incidentId}/recommendation`),

  requestApproval: (recommendationId: number) =>
    request<{ approval: Approval }>(
      `/api/recommendations/${recommendationId}/approval-request`,
      { method: "POST" },
    ),

  /**
   * Submit a human decision. The browser cannot approve anything on its own — this
   * either persists server-side or throws, and the UI only ever renders what came back.
   */
  decideApproval: (approvalId: number, decision: "APPROVED" | "REJECTED", approver: string, note?: string) =>
    request<{ approval: Approval }>(`/api/approvals/${approvalId}/decision`, {
      method: "POST",
      body: { decision, approver_identity: approver, note },
    }),

  expireStaleApprovals: () =>
    request<{ expired: number }>("/api/approvals/expire-stale", { method: "POST" }),

  /** Execution happens in the backend through SimulatedRoutingAdapter. Never here. */
  execute: (recommendationId: number, executedBy?: string) =>
    request<{ action: Action | null; rejected: boolean }>(
      `/api/recommendations/${recommendationId}/execute`,
      { method: "POST", body: { executed_by: executedBy }, timeoutMs: 60_000 },
    ),

  action: (actionId: number) =>
    request<{ action: Action; verification: Verification | null }>(`/api/actions/${actionId}`),

  verify: (actionId: number) =>
    request<{ verification: Verification }>(`/api/actions/${actionId}/verify`, {
      method: "POST",
      timeoutMs: 60_000,
    }),

  verification: (actionId: number) =>
    request<{ verification: Verification | null }>(`/api/actions/${actionId}/verification`),

  batchRecovery: () => request<{ batch: BatchRecovery }>("/api/batch/recovery"),

  audit: (incidentId: number) =>
    request<{ incident_id: number; events: AuditEvent[] }>(`/api/incidents/${incidentId}/audit`),

  agentRun: (incidentId: number) =>
    request<{ agent_run: AgentRun | null; detail?: string }>(`/api/incidents/${incidentId}/agent`),

  runAgent: (incidentId: number) =>
    request<{ agent_run_id: number | null; status: string; final_state: string }>(
      `/api/incidents/${incidentId}/agent/analyze`,
      { method: "POST", timeoutMs: AGENT_TIMEOUT_MS },
    ),

  demoReset: () =>
    request<{ reset: boolean; cleared: Record<string, number>; preserved: Record<string, unknown> }>(
      "/api/demo/reset",
      { method: "POST", timeoutMs: 60_000 },
    ),
};

export { BASE_URL };
