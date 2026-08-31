import { useCallback, useState } from "react";
import { cx, SeverityBadge, StatusPill } from "../components/ui";
import { ErrorState, LoadingPanel } from "../components/states";
import { AgentPanel } from "../components/AgentPanel";
import { DecisionState } from "./DecisionState";
import { CommandCenter } from "./tabs/CommandCenter";
import { EvidenceRCA } from "./tabs/EvidenceRCA";
import { Simulation } from "./tabs/Simulation";
import { Recommendation } from "./tabs/Recommendation";
import { Approval } from "./tabs/Approval";
import { ExecutionVerification } from "./tabs/ExecutionVerification";
import { api } from "../lib/api";
import { useResource, usePolling } from "../lib/hooks";
import { ts } from "../lib/format";
import { incidentStatusLabel, recoveryTone } from "../lib/recovery";
import type { Severity } from "../lib/types";

export type IncidentTab =
  | "overview"
  | "evidence"
  | "simulation"
  | "recommendation"
  | "approval"
  | "verification";

const tabs: { key: IncidentTab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "evidence", label: "Evidence & RCA" },
  { key: "simulation", label: "Simulation" },
  { key: "recommendation", label: "Recommendation" },
  { key: "approval", label: "Approval" },
  { key: "verification", label: "Verification" },
];

export function IncidentWorkspace({
  incidentId,
  agentReachable,
  onOpenAudit,
  onWorkflowChanged,
}: {
  incidentId: number;
  agentReachable: boolean;
  onOpenAudit: () => void;
  onWorkflowChanged: () => void;
}) {
  const [tab, setTab] = useState<IncidentTab>("overview");

  const incident = useResource(() => api.incident(incidentId), [incidentId]);
  const bundle = useResource(() => api.recommendation(incidentId), [incidentId]);
  const simulations = useResource(() => api.simulations(incidentId), [incidentId]);
  const agent = useResource(() => api.agentRun(incidentId), [incidentId]);

  // The workflow's authoritative state comes from the recommendation bundle, which the
  // backend recomputes from persisted rows. There is no local `approved` flag anywhere
  // in this component -- a refresh reconstructs the same truth (§2, §24, §41).
  const recovery = bundle.data?.recovery ?? incident.data?.recovery ?? null;

  /** After any mutation, re-read everything that could have changed. Never predict. */
  const refreshAll = useCallback(async () => {
    await Promise.all([bundle.refresh(), incident.refresh(), simulations.refresh()]);
    onWorkflowChanged();
  }, [bundle.refresh, incident.refresh, simulations.refresh, onWorkflowChanged]);

  // Execution is genuinely transient: the action exists but verification has not run.
  // Poll only in that window, and stop the moment it closes (§34).
  usePolling(refreshAll, recovery?.state === "VERIFYING", 5000);

  if (incident.initialLoading) {
    return (
      <div className="px-8 py-6">
        <LoadingPanel label="Loading incident" rows={6} />
      </div>
    );
  }
  if (incident.error && !incident.data) {
    return (
      <div className="px-8 py-6">
        <ErrorState error={incident.error} onRetry={() => void incident.refresh()} />
      </div>
    );
  }
  if (!incident.data?.incident) {
    return (
      <div className="px-8 py-6">
        <p className="text-[13px] text-muted-foreground">This incident has no analysis to display.</p>
      </div>
    );
  }

  const inc = incident.data.incident;
  const rca = incident.data.rca;
  const severity = (rca?.severity ?? "LOW") as Severity;
  const status = incidentStatusLabel(recovery?.state ?? "NO_ACTIVE_ACTION");

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border bg-surface-0 px-8 py-5">
        <div className="flex items-start justify-between gap-6">
          <div className="flex items-start gap-4">
            <div className="mt-0.5 flex size-10 items-center justify-center rounded-md bg-[color:var(--critical)]/12 ring-1 ring-[color:var(--critical)]/30">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="text-[color:var(--critical)]" aria-hidden>
                <path d="M12 3 2 20h20L12 3Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
                <path d="M12 10v4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                <circle cx="12" cy="17" r="1" fill="currentColor" />
              </svg>
            </div>
            <div>
              <div className="flex items-center gap-3">
                <SeverityBadge severity={severity} />
                <span className="text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
                  {rca?.predicted_root_cause ?? "Payment degradation detected"}
                </span>
              </div>
              <h1 className="mt-1.5 font-display text-2xl font-semibold tracking-tight text-foreground">
                {inc.incident_name} ·{" "}
                <span className="font-mono text-[color:var(--accent)]">
                  {inc.affected_gateway ?? "systemic"}
                </span>
              </h1>
              <p className="mt-0.5 text-sm text-muted-foreground">
                {ts(inc.start)} → {ts(inc.end)}
              </p>
            </div>
          </div>
          <div className="flex flex-col items-end gap-2">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[11px] text-faint-foreground">
                INC-{String(inc.incident_id).padStart(4, "0")}
              </span>
              <StatusPill tone={recoveryTone(recovery?.state ?? "")} dot>
                {status}
              </StatusPill>
            </div>
            <StatusPill tone="warning">Simulation Mode</StatusPill>
          </div>
        </div>

        <nav className="mt-5 flex items-center gap-1 overflow-x-auto" aria-label="Incident workflow">
          {tabs.map((t, i) => {
            const active = tab === t.key;
            return (
              <div key={t.key} className="flex items-center">
                <button
                  onClick={() => setTab(t.key)}
                  aria-current={active ? "step" : undefined}
                  className={cx(
                    "flex items-center gap-2 rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors",
                    active
                      ? "bg-surface-2 text-foreground"
                      : "text-muted-foreground hover:bg-surface-1 hover:text-foreground",
                  )}
                >
                  <span
                    className={cx(
                      "tnum flex items-center justify-center rounded-full text-[10px] font-semibold",
                      active ? "bg-accent text-white" : "bg-surface-3 text-muted-foreground",
                    )}
                    style={{ width: 18, height: 18 }}
                  >
                    {i + 1}
                  </span>
                  {t.label}
                </button>
                {i < tabs.length - 1 && <span className="px-0.5 text-faint-foreground" aria-hidden>›</span>}
              </div>
            );
          })}
        </nav>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div className="min-w-0 flex-1 overflow-y-auto px-8 py-6">
          {tab === "overview" && (
            <CommandCenter incident={incident} bundle={bundle} agent={agent} onGo={setTab} />
          )}
          {tab === "evidence" && <EvidenceRCA incident={incident} onGo={setTab} />}
          {tab === "simulation" && (
            <Simulation
              incidentId={incidentId}
              simulations={simulations}
              bundle={bundle}
              onAnalyzed={refreshAll}
              onGo={setTab}
            />
          )}
          {tab === "recommendation" && (
            <Recommendation bundle={bundle} simulations={simulations} onGo={setTab} />
          )}
          {tab === "approval" && <Approval bundle={bundle} onChanged={refreshAll} onGo={setTab} />}
          {tab === "verification" && (
            <ExecutionVerification bundle={bundle} onChanged={refreshAll} onOpenAudit={onOpenAudit} />
          )}
        </div>

        <aside className="hidden w-[320px] shrink-0 space-y-4 overflow-y-auto border-l border-border bg-surface-0 px-5 py-6 xl:block">
          <AgentPanel
            incidentId={incidentId}
            resource={agent}
            reachable={agentReachable}
            recommendation={bundle.data?.recommendation ?? null}
          />
          <DecisionState recovery={recovery} tab={tab} onGo={setTab} />
        </aside>
      </div>
    </div>
  );
}
