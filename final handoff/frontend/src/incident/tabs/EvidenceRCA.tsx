import { useState } from "react";
import { Panel, StatusPill, TruthTag, MonoField, Meter, Kicker, cx } from "../../components/ui";
import { ErrorState, LoadingPanel, EmptyState } from "../../components/states";
import type { Resource } from "../../lib/hooks";
import type { DetectionView, IncidentDetail } from "../../lib/types";
import type { IncidentTab } from "../IncidentWorkspace";
import { fp, inr, num, pct, sigma } from "../../lib/format";

/**
 * Evidence and root-cause analysis — Day 3 truth, rendered as-is.
 *
 * The PRIMARY / DERIVATIVE distinction is preserved exactly (Day 3 P1-1): a derivative
 * alert is statistically real but causally explained by the primary, and presenting the
 * two as equals would re-introduce the alert storm that fix removed.
 */
export function EvidenceRCA({
  incident,
  onGo,
}: {
  incident: Resource<IncidentDetail>;
  onGo: (t: IncidentTab) => void;
}) {
  const [showDerivative, setShowDerivative] = useState(false);

  if (incident.initialLoading) return <LoadingPanel label="Loading evidence" rows={6} />;
  if (incident.error && !incident.data) {
    return <ErrorState error={incident.error} onRetry={() => void incident.refresh()} />;
  }
  if (!incident.data) return null;

  const { rca, detections, derivative_detections, evidence } = incident.data;
  const supporting = new Set(rca?.supporting_evidence_ids ?? []);

  return (
    <div className="space-y-6">
      <Panel
        kicker="Root Cause Analysis"
        title={rca?.predicted_root_cause ?? "UNAVAILABLE"}
        right={<TruthTag truth="DETERMINISTIC" />}
      >
        {!rca ? (
          <EmptyState title="No RCA result" detail="This incident has no root-cause analysis." />
        ) : (
          <>
            <p className="text-[13px] leading-relaxed text-muted-foreground">{rca.explanation}</p>

            {/* Confidence and evidence strength are shown SEPARATELY, never blended into
                a single score — a Day 3 P1-2 property an action gate depends on. */}
            <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Gauge label="Verdict" text={rca.verdict} />
              <Gauge label="Confidence" text={`${pct(rca.confidence)}%`} value={rca.confidence ?? 0} />
              <Gauge label="Evidence strength" text={`${pct(rca.evidence_strength)}%`} value={rca.evidence_strength ?? 0} />
              <Gauge label="Significance" text={sigma(rca.significance_sigma)} />
            </div>

            <div className="mt-5 space-y-0.5 border-t border-border pt-4">
              <MonoField label="Analysis run" value={String(rca.analysis_run_id)} />
              <MonoField label="Severity" value={rca.severity} />
              <MonoField label="Hypothesis type" value={rca.predicted_hypothesis_type ?? "UNAVAILABLE"} />
              <MonoField label="RCA fingerprint" value={fp(rca.rca_fingerprint, 20)} />
            </div>

            {rca.alternatives_considered.length > 0 && (
              <div className="mt-5 border-t border-border pt-4">
                <Kicker>Alternatives considered and rejected</Kicker>
                <ul className="mt-2 space-y-1.5">
                  {rca.alternatives_considered.map((alt, i) => (
                    <li key={i} className="font-mono text-[11px] leading-relaxed text-muted-foreground">
                      {typeof alt === "object" ? JSON.stringify(alt) : String(alt)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </Panel>

      <Panel
        kicker="Detections"
        title={`${detections.length} primary · ${derivative_detections.length} derivative`}
        right={
          derivative_detections.length > 0 ? (
            <button
              onClick={() => setShowDerivative((s) => !s)}
              aria-expanded={showDerivative}
              className="text-[12px] text-muted-foreground transition-colors hover:text-foreground"
            >
              {showDerivative ? "Hide" : "Show"} derivative
            </button>
          ) : undefined
        }
        flush
      >
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Detected anomalies with causal role</caption>
            <thead>
              <tr className="border-b border-border text-[10px] uppercase tracking-wide text-faint-foreground">
                <th scope="col" className="px-5 py-2.5 text-left font-medium">Cohort</th>
                <th scope="col" className="px-3 py-2.5 text-left font-medium">Role</th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">Significance</th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">Population</th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">GMV at Risk</th>
                <th scope="col" className="px-5 py-2.5 text-left font-medium">Explained by</th>
              </tr>
            </thead>
            <tbody>
              {detections.map((d) => <DetectionRow key={d.anomaly_id} d={d} />)}
              {showDerivative && derivative_detections.map((d) => <DetectionRow key={d.anomaly_id} d={d} muted />)}
            </tbody>
          </table>
        </div>
        {!showDerivative && derivative_detections.length > 0 && (
          <p className="border-t border-border px-5 py-3 text-[12px] leading-relaxed text-faint-foreground">
            {derivative_detections.length} derivative alert
            {derivative_detections.length === 1 ? " is" : "s are"} hidden. They are statistically
            real but causally downstream of the primary cause, and are never actioned as
            independent causes.
          </p>
        )}
      </Panel>

      <Panel kicker="Evidence" title={`${evidence.length} records`} flush>
        {evidence.length === 0 ? (
          <EmptyState title="No evidence" />
        ) : (
          <ul className="divide-y divide-border">
            {evidence.slice(0, 20).map((e) => (
              <li key={e.evidence_id} className="px-5 py-3.5">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[11px] text-[color:var(--accent)]">
                        E-{e.evidence_id}
                      </span>
                      <span className="text-[13px] font-medium text-foreground">{e.metric}</span>
                      {supporting.has(e.evidence_id) && (
                        <StatusPill tone="success">SUPPORTING</StatusPill>
                      )}
                    </div>
                    <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
                      {e.explanation}
                    </p>
                  </div>
                  <div className="tnum shrink-0 text-right">
                    <div className="font-mono text-[12px] text-foreground">{sigma(e.significance_sigma)}</div>
                    <div className="mt-0.5 font-mono text-[11px] text-faint-foreground">
                      {e.baseline != null && e.current != null
                        ? `${e.baseline.toFixed(4)} → ${e.current.toFixed(4)}`
                        : "UNAVAILABLE"}
                    </div>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
        {evidence.length > 20 && (
          <p className="border-t border-border px-5 py-3 text-[12px] text-faint-foreground">
            Showing the 20 strongest of {evidence.length} evidence records.
          </p>
        )}
      </Panel>

      <div className="flex justify-end">
        <button
          onClick={() => onGo("simulation")}
          className="text-[13px] font-medium text-[color:var(--accent)] hover:underline"
        >
          Continue to simulation →
        </button>
      </div>
    </div>
  );
}

function DetectionRow({ d, muted }: { d: DetectionView; muted?: boolean }) {
  return (
    <tr className={cx("border-b border-border last:border-0", muted && "opacity-70")}>
      <td className="px-5 py-3.5 font-mono text-[12px] text-foreground">{d.cohort_key}</td>
      <td className="px-3 py-3.5">
        <StatusPill tone={d.alert_role === "PRIMARY" ? "critical" : "muted"}>{d.alert_role}</StatusPill>
      </td>
      <td className="tnum px-3 py-3.5 text-right font-medium text-foreground">{sigma(d.significance_sigma)}</td>
      <td className="tnum px-3 py-3.5 text-right text-muted-foreground">{num(d.affected_population)}</td>
      <td className="tnum px-3 py-3.5 text-right text-muted-foreground">{inr(d.gmv_at_risk)}</td>
      <td className="px-5 py-3.5 font-mono text-[11px] text-faint-foreground">
        {d.derived_from_anomaly_id ? `anomaly ${d.derived_from_anomaly_id}` : "—"}
      </td>
    </tr>
  );
}

function Gauge({ label, text, value }: { label: string; text: string; value?: number }) {
  return (
    <div className="rounded-md border border-border bg-surface-0 p-3">
      <div className="text-[10px] font-medium uppercase tracking-[0.09em] text-muted-foreground">{label}</div>
      <div className="tnum mt-1.5 font-display text-lg font-semibold text-foreground">{text}</div>
      {value !== undefined && <div className="mt-2"><Meter value={value} tone="accent" /></div>}
    </div>
  );
}
