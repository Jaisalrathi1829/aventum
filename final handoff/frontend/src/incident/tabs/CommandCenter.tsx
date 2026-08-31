import { Panel, Metric, Button, StatusPill, TruthTag, MonoField } from "../../components/ui";
import { ErrorState, LoadingPanel, StoppedState } from "../../components/states";
import { GatewayHealthChart } from "../../components/GatewayHealthChart";
import type { Resource } from "../../lib/hooks";
import type { AgentRun, IncidentDetail, RecommendationBundle } from "../../lib/types";
import type { IncidentTab } from "../IncidentWorkspace";
import { inr, num, pct, sigma } from "../../lib/format";
import { isStopped, recoveryLabel, recoveryTone } from "../../lib/recovery";

/**
 * Incident command centre: what happened, how serious, what it costs, what is next.
 *
 * Every number is the backend's. The only judgement made here is which panel to show,
 * and that follows the backend's own `recovery.state`.
 */
export function CommandCenter({
  incident,
  bundle,
  agent,
  onGo,
}: {
  incident: Resource<IncidentDetail>;
  bundle: Resource<RecommendationBundle>;
  agent: Resource<{ agent_run: AgentRun | null }>;
  onGo: (t: IncidentTab) => void;
}) {
  if (incident.initialLoading) return <LoadingPanel label="Loading incident overview" rows={6} />;
  if (incident.error && !incident.data) {
    return <ErrorState error={incident.error} onRetry={() => void incident.refresh()} />;
  }
  if (!incident.data) return null;

  const d = incident.data;
  const rca = d.rca;
  const primary = d.detections[0] ?? null;
  const recovery = bundle.data?.recovery ?? d.recovery;
  const rec = bundle.data?.recommendation ?? null;

  // The affected gateway's own before/after, straight from the engine.
  const affected = d.gateway_health.find((g) => g.is_affected) ?? null;

  return (
    <div className="space-y-6">
      {/* What is happening, and how bad. */}
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius)] border border-border bg-border md:grid-cols-4">
        <div className="bg-surface-1 p-4">
          <Metric label="Significance" value={sigma(rca?.significance_sigma)} truth="DETERMINISTIC" big />
        </div>
        <div className="bg-surface-1 p-4">
          <Metric label="RCA Confidence" value={pct(rca?.confidence)} unit="%" truth="DETERMINISTIC" big />
        </div>
        <div className="bg-surface-1 p-4">
          <Metric label="Evidence Strength" value={pct(rca?.evidence_strength)} unit="%" truth="DETERMINISTIC" big />
        </div>
        <div className="bg-surface-1 p-4">
          <Metric
            label="GMV at Risk"
            value={primary ? inr(primary.gmv_at_risk) : "UNAVAILABLE"}
            truth="OBSERVED"
            big
            sub="Observed amounts, modelled outcomes"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-6">
          <Panel
            kicker="Gateway Health"
            title="Failure probability under this incident"
            right={
              affected ? (
                <StatusPill tone="critical" dot>
                  {affected.gateway_id} degraded
                </StatusPill>
              ) : (
                <StatusPill tone="muted">No single gateway</StatusPill>
              )
            }
          >
            <GatewayHealthChart gateways={d.gateway_health} />
            {affected?.incident_failure_multiplier != null && (
              <p className="mt-3 text-[12px] leading-relaxed text-muted-foreground">
                The incident applies a{" "}
                <span className="tnum font-mono text-foreground">
                  {affected.incident_failure_multiplier}×
                </span>{" "}
                failure multiplier to {affected.gateway_id}, moving it from{" "}
                <span className="tnum font-mono text-foreground">
                  {pct(affected.baseline_failure_probability)}%
                </span>{" "}
                to{" "}
                <span className="tnum font-mono text-[color:var(--critical)]">
                  {pct(affected.effective_failure_probability)}%
                </span>{" "}
                modelled failure probability.
              </p>
            )}
          </Panel>

          {/* Root cause — a deterministic verdict, not the agent's opinion. */}
          <Panel
            kicker="Root Cause"
            title={rca?.predicted_root_cause ?? "UNAVAILABLE"}
            right={<TruthTag truth="DETERMINISTIC" />}
          >
            {rca ? (
              <>
                <p className="text-[13px] leading-relaxed text-muted-foreground">{rca.summary}</p>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <StatusPill tone={rca.verdict === "CONFIDENT" ? "success" : "warning"}>
                    {rca.verdict}
                  </StatusPill>
                  <span className="text-[12px] text-faint-foreground">
                    {rca.supporting_evidence_ids.length} supporting ·{" "}
                    {rca.contradicting_evidence_ids.length} contradicting
                  </span>
                  <Button variant="ghost" className="ml-auto" onClick={() => onGo("evidence")}>
                    Evidence & RCA →
                  </Button>
                </div>
              </>
            ) : (
              <p className="text-[13px] text-faint-foreground">No RCA result is available.</p>
            )}
          </Panel>
        </div>

        <div className="space-y-6">
          <Panel kicker="Current State" title={recoveryLabel(recovery.state)}>
            <StatusPill tone={recoveryTone(recovery.state)} dot>
              {recovery.state.replace(/_/g, " ")}
            </StatusPill>
            <p className="mt-3 text-[13px] leading-relaxed text-muted-foreground">{recovery.detail}</p>

            {isStopped(recovery.state) && (
              <div className="mt-4">
                <StoppedState
                  title="Stopped"
                  why={recovery.detail}
                  next="No further intervention will be attempted for this recommendation."
                  tone={recovery.state === "NO_ACTION" ? "muted" : "warning"}
                />
              </div>
            )}

            {rec && (
              <div className="mt-4 space-y-1 border-t border-border pt-4">
                <MonoField label="Proposed" value={rec.action_type} />
                {rec.action_type !== "NO_ACTION" && (
                  <>
                    <MonoField
                      label="Route"
                      value={`${rec.source_gateway_id ?? "—"} → ${rec.target_gateway_id ?? "—"}`}
                    />
                    <MonoField label="Allocation" value={`${num(rec.traffic_percentage)}%`} />
                  </>
                )}
                <MonoField label="Policy" value={rec.policy.validation_result} />
              </div>
            )}

            <Button variant="primary" full className="mt-4" onClick={() => onGo("simulation")}>
              Review simulations →
            </Button>
          </Panel>

          <Panel kicker="Detections" title={`${d.detections.length} primary`}>
            {d.detections.length === 0 ? (
              <p className="text-[13px] text-faint-foreground">No primary detection.</p>
            ) : (
              <ul className="space-y-3">
                {d.detections.map((det) => (
                  <li key={det.anomaly_id} className="rounded-md border border-border bg-surface-0 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[12px] text-[color:var(--accent)]">{det.cohort_key}</span>
                      <StatusPill tone="critical">{det.alert_role}</StatusPill>
                    </div>
                    <div className="tnum mt-2 flex items-center gap-3 text-[11px] text-muted-foreground">
                      <span>{sigma(det.significance_sigma)}</span>
                      <span>·</span>
                      <span>{num(det.affected_population)} txns</span>
                      <span>·</span>
                      <span>{inr(det.gmv_at_risk)}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {d.derivative_detections.length > 0 && (
              <p className="mt-3 text-[11px] leading-relaxed text-faint-foreground">
                {d.derivative_detections.length} derivative alert
                {d.derivative_detections.length === 1 ? "" : "s"} are statistically real but
                causally explained by the primary. They are never actioned independently.
              </p>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
