import { Panel, Button, StatusPill, TruthTag, MonoField, Kicker } from "../../components/ui";
import { ErrorState, LoadingPanel, EmptyState, StoppedState } from "../../components/states";
import type { Resource } from "../../lib/hooks";
import type { EnvironmentNotice, RecommendationBundle, Simulation as Sim } from "../../lib/types";
import type { IncidentTab } from "../IncidentWorkspace";
import { fp, inr, ms, num, pct, pctDelta, ts } from "../../lib/format";

/**
 * The persisted recommendation.
 *
 * The governing property of this screen is that AI PROPOSES and POLICY DECIDES (§15).
 * The agent's rationale and the deterministic policy verdict are rendered as two
 * separately attributed blocks, and this component never evaluates whether an action is
 * permitted — it reads `policy.validation_result` and shows it.
 */
export function Recommendation({
  bundle,
  simulations,
  onGo,
}: {
  bundle: Resource<RecommendationBundle>;
  simulations: Resource<{ environment: EnvironmentNotice; simulations: Sim[] }>;
  onGo: (t: IncidentTab) => void;
}) {
  if (bundle.initialLoading) return <LoadingPanel label="Loading recommendation" rows={6} />;
  if (bundle.error && !bundle.data) {
    return <ErrorState error={bundle.error} onRetry={() => void bundle.refresh()} />;
  }

  const rec = bundle.data?.recommendation ?? null;
  const stale = bundle.data?.stale ?? null;

  if (!rec) {
    return (
      <Panel kicker="Recommendation" title="No recommendation">
        <EmptyState
          title="Nothing has been recommended"
          detail="Run the deterministic analysis from the Simulation tab to produce one."
        />
        <Button variant="secondary" full onClick={() => onGo("simulation")}>
          Go to simulation
        </Button>
      </Panel>
    );
  }

  const permitted = rec.policy.validation_result === "PERMITTED";
  const isNoAction = rec.action_type === "NO_ACTION";
  const sim = simulations.data?.simulations.find((s) => s.simulation_id === rec.simulation_id) ?? null;

  return (
    <div className="space-y-6">
      {/* A stale recommendation must never look executable (§26). */}
      {stale?.is_stale && (
        <StoppedState
          title="Stale"
          why={stale.reasons.join(" ")}
          next={stale.next_step}
          tone="warning"
        />
      )}

      <Panel
        kicker="Proposed Action"
        title={
          isNoAction
            ? "NO_ACTION"
            : `${rec.action_type} · ${rec.source_gateway_id} → ${rec.target_gateway_id} @ ${num(rec.traffic_percentage)}%`
        }
        right={<StatusPill tone={permitted ? "success" : "critical"} dot>{rec.policy.validation_result}</StatusPill>}
      >
        {isNoAction ? (
          <StoppedState
            title="No action"
            why="The deterministic decision is that no intervention is justified for this incident."
            next="Aventum will not propose an intervention. Doing nothing is a valid, safe outcome."
            tone="muted"
          />
        ) : (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Fig label="Projected GMV retained" value={inr(rec.expected.expected_gmv_retained)} tone="projected" truth />
            <Fig label="Projected Δ success" value={pctDelta(rec.expected.expected_success_delta)} tone="success" truth />
            <Fig label="Projected Δ latency" value={ms(rec.expected.expected_latency_delta_ms)} />
            <Fig label="Risk score" value={rec.risk.risk_score?.toFixed(4) ?? "UNAVAILABLE"} />
          </div>
        )}

        <p className="mt-4 text-[11px] leading-relaxed text-faint-foreground">
          These are PROJECTIONS produced before any action. They are not recovered money and
          not a measured outcome.
        </p>
      </Panel>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Policy — deterministic authority. */}
        <Panel kicker="Policy Decision" title="Deterministic authority" right={<TruthTag truth="DETERMINISTIC" />}>
          <div className="flex items-center gap-2">
            <StatusPill tone={permitted ? "success" : "critical"} dot>
              {rec.policy.validation_result}
            </StatusPill>
            <span className="font-mono text-[11px] text-faint-foreground">{rec.policy.policy_version}</span>
          </div>

          {!permitted && (
            <div className="mt-4">
              <StoppedState
                title="Blocked by policy"
                why="The policy gate refused this action. It will not be presented for approval."
                next="No approval can be requested for a blocked recommendation."
                tone="critical"
              />
            </div>
          )}

          <div className="mt-4">
            {/* Reason codes explain a REFUSAL, so Day 4A records them only when the gate
                blocks. For a permitted recommendation the meaningful disclosure is the
                set of thresholds it had to clear -- also persisted, also the backend's. */}
            <Kicker>{rec.policy.reason_codes ? "Gate results" : "Thresholds satisfied"}</Kicker>
            <ReasonCodes codes={rec.policy.reason_codes ?? rec.policy.constraints} />
          </div>

          <p className="mt-4 text-[11px] leading-relaxed text-faint-foreground">
            The agent cannot influence this verdict, and this interface cannot override it.
          </p>
        </Panel>

        {/* Diagnosis inputs, carried separately as the gate required them. */}
        <Panel kicker="Decision Inputs" title="Four independent signals" right={<TruthTag truth="DETERMINISTIC" />}>
          <div className="grid grid-cols-2 gap-4">
            <Fig label="Confidence" value={`${pct(rec.diagnosis.confidence)}%`} />
            <Fig label="Evidence strength" value={`${pct(rec.diagnosis.evidence_strength)}%`} />
            <Fig label="Significance" value={`${rec.diagnosis.significance_sigma?.toFixed(2) ?? "—"}σ`} />
            <Fig label="Severity" value={rec.diagnosis.severity ?? "UNAVAILABLE"} />
          </div>
          <p className="mt-4 text-[11px] leading-relaxed text-faint-foreground">
            Kept as four separate values so no single confidence scalar can authorise a
            larger intervention.
          </p>
        </Panel>
      </div>

      {/* Agent rationale — attributed, and explicitly not the authority. */}
      {rec.rationale && (
        <Panel kicker="Agent Rationale" title="Interpretation" right={<TruthTag truth="AGENT" />}>
          <p className="text-[13px] leading-relaxed text-muted-foreground">{rec.rationale}</p>
          <p className="mt-3 text-[11px] leading-relaxed text-faint-foreground">
            AI-generated interpretation. It did not compute any number above and did not decide
            whether this action is permitted.
          </p>
        </Panel>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel kicker="Provenance" title="Lineage">
          <div className="space-y-0.5">
            <MonoField label="Recommendation" value={String(rec.recommendation_id)} />
            <MonoField label="Cites simulation" value={String(rec.simulation_id)} />
            <MonoField label="Analysis run" value={String(rec.analysis_run_id)} />
            <MonoField label="Agent run" value={rec.agent_run_id ? String(rec.agent_run_id) : "NONE (deterministic)"} />
            <MonoField label="Fingerprint" value={fp(rec.recommendation_fingerprint, 20)} />
            <MonoField label="Simulation fingerprint" value={fp(sim?.identity.simulation_fingerprint, 20)} />
            <MonoField label="Status" value={rec.status} />
            <MonoField label="Expires" value={ts(rec.expires_at)} />
          </div>
        </Panel>

        <Panel kicker="Alternatives" title={`${rec.alternatives_considered?.length ?? 0} rejected`}>
          {!rec.alternatives_considered?.length ? (
            <p className="text-[13px] text-faint-foreground">No alternatives recorded.</p>
          ) : (
            <ul className="space-y-2">
              {rec.alternatives_considered.map((alt, i) => (
                <li key={i} className="rounded-md border border-border bg-surface-0 p-2.5 font-mono text-[11px] leading-relaxed text-muted-foreground">
                  {typeof alt === "object" ? JSON.stringify(alt) : String(alt)}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      {permitted && !isNoAction && !stale?.is_stale && (
        <div className="flex justify-end">
          <Button variant="primary" onClick={() => onGo("approval")}>
            Continue to human approval →
          </Button>
        </div>
      )}
    </div>
  );
}

function ReasonCodes({ codes }: { codes: Record<string, unknown> | null }) {
  const entries = Object.entries(codes ?? {});
  if (entries.length === 0) {
    return <p className="mt-2 font-mono text-[11px] text-faint-foreground">UNAVAILABLE</p>;
  }
  return (
    <ul className="mt-2 space-y-1">
      {entries.map(([k, v]) => {
        const passed = v === true || v === "PASS" || v === "PERMITTED";
        const failed = v === false || v === "FAIL" || v === "BLOCKED";
        return (
          <li key={k} className="flex items-center justify-between gap-3 border-b border-border/60 py-1 last:border-0">
            <span className="font-mono text-[11px] text-muted-foreground">{k}</span>
            <span
              className="font-mono text-[11px] font-medium"
              style={{ color: failed ? "var(--critical)" : passed ? "var(--success)" : "var(--muted-foreground)" }}
            >
              {typeof v === "object" ? JSON.stringify(v) : String(v)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function Fig({
  label,
  value,
  tone,
  truth,
}: {
  label: string;
  value: string;
  tone?: "projected" | "success";
  truth?: boolean;
}) {
  const color =
    tone === "projected" ? "var(--projected)" : tone === "success" ? "var(--success)" : "var(--foreground)";
  return (
    <div>
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] font-medium uppercase tracking-[0.09em] text-muted-foreground">{label}</span>
        {truth && <TruthTag truth="PROJECTED" />}
      </div>
      <div className="tnum mt-1 font-display text-lg font-semibold" style={{ color }}>
        {value}
      </div>
    </div>
  );
}
