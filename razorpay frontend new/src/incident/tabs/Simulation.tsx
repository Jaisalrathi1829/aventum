import { useState } from "react";
import { Panel, Button, StatusPill, TruthTag, MonoField, Kicker, cx } from "../../components/ui";
import { ErrorState, LoadingPanel, EmptyState } from "../../components/states";
import { api } from "../../lib/api";
import { useMutation, type Resource } from "../../lib/hooks";
import type { EnvironmentNotice, RecommendationBundle, Simulation as Sim } from "../../lib/types";
import type { IncidentTab } from "../IncidentWorkspace";
import { fp, inr, ms, num, pct, pctDelta } from "../../lib/format";

/**
 * The simulation workspace: bounded counterfactuals over one identical world.
 *
 * Every figure is labelled PROJECTED, because none of it has happened. The NO_ACTION
 * baseline is shown alongside the interventions rather than hidden, since "do nothing"
 * is a candidate the system is genuinely allowed to choose.
 */
export function Simulation({
  incidentId,
  simulations,
  bundle,
  onAnalyzed,
  onGo,
}: {
  incidentId: number;
  simulations: Resource<{ environment: EnvironmentNotice; simulations: Sim[] }>;
  bundle: Resource<RecommendationBundle>;
  onAnalyzed: () => Promise<void>;
  onGo: (t: IncidentTab) => void;
}) {
  const [selected, setSelected] = useState<number | null>(null);

  const analyze = useMutation(async () => {
    const result = await api.analyze(incidentId);
    await onAnalyzed();
    return result;
  });

  if (simulations.initialLoading) return <LoadingPanel label="Loading simulations" rows={6} />;
  if (simulations.error && !simulations.data) {
    return <ErrorState error={simulations.error} onRetry={() => void simulations.refresh()} />;
  }

  const sims = simulations.data?.simulations ?? [];
  const chosenSimulationId = bundle.data?.recommendation?.simulation_id ?? null;
  const noAction = sims.find((s) => s.action_type === "NO_ACTION") ?? null;
  const candidates = sims.filter((s) => s.action_type !== "NO_ACTION");
  const detail = sims.find((s) => s.simulation_id === selected) ?? null;

  if (sims.length === 0) {
    return (
      <div className="space-y-4">
        <Panel kicker="Simulation" title="No candidates simulated yet">
          <EmptyState
            title="Nothing has been simulated"
            detail="Run the deterministic decision pipeline to sweep the bounded candidate space, apply policy, and produce a recommendation. No agent is required."
          />
          <Button variant="primary" full onClick={() => void analyze.run()} disabled={analyze.pending}>
            {analyze.pending ? "Running deterministic analysis…" : "Run deterministic analysis"}
          </Button>
          {analyze.error && <div className="mt-3"><ErrorState error={analyze.error} compact /></div>}
        </Panel>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {noAction && (
        <Panel
          kicker="Baseline"
          title="NO_ACTION — what happens if nothing is done"
          right={<TruthTag truth="SIMULATED" />}
        >
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Fig label="Projected success rate" value={`${pct(noAction.projected.projected_success_rate)}%`} />
            <Fig label="Projected failures" value={num(noAction.projected.projected_failure_count)} />
            <Fig label="GMV at risk" value={inr(noAction.projected.projected_gmv_at_risk)} tone="critical" />
            <Fig label="Population" value={num(noAction.affected_population)} />
          </div>
          <p className="mt-4 text-[12px] leading-relaxed text-faint-foreground">
            This is the comparison every intervention must beat. It is a real simulated
            candidate, not an assumption.
          </p>
        </Panel>
      )}

      <Panel
        kicker="Candidate Simulations"
        title={`${candidates.length} bounded ${candidates.length === 1 ? "candidate" : "candidates"}`}
        right={<TruthTag truth="PROJECTED" />}
        flush
      >
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Projected outcome of each bounded reroute candidate</caption>
            <thead>
              <tr className="border-b border-border text-[10px] uppercase tracking-wide text-faint-foreground">
                <th scope="col" className="px-5 py-2.5 text-left font-medium">Candidate</th>
                <th scope="col" className="px-3 py-2.5 text-left font-medium">Status</th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">Proj. success</th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">Δ success</th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">Proj. GMV retained</th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">Δ latency</th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">Risk</th>
                <th scope="col" className="px-5 py-2.5 text-right font-medium" />
              </tr>
            </thead>
            <tbody>
              {candidates.map((s) => {
                const chosen = s.simulation_id === chosenSimulationId;
                const invalid = s.status !== "VALID";
                return (
                  <tr
                    key={s.simulation_id}
                    className={cx(
                      "border-b border-border last:border-0 transition-colors hover:bg-surface-2",
                      chosen && "bg-[color:var(--accent)]/[0.06]",
                      invalid && "opacity-60",
                    )}
                  >
                    <td className="px-5 py-3.5">
                      <div className="font-mono text-[12px] text-foreground">
                        {s.source_gateway_id} → {s.target_gateway_id}
                      </div>
                      <div className="tnum font-mono text-[11px] text-faint-foreground">
                        {num(s.traffic_percentage)}% · sim {s.simulation_id}
                      </div>
                    </td>
                    <td className="px-3 py-3.5">
                      {chosen ? (
                        <StatusPill tone="accent" dot>SELECTED</StatusPill>
                      ) : invalid ? (
                        <StatusPill tone="muted">{s.status}</StatusPill>
                      ) : (
                        <StatusPill tone="muted">VALID</StatusPill>
                      )}
                    </td>
                    <td className="tnum px-3 py-3.5 text-right text-foreground">
                      {pct(s.projected.projected_success_rate)}%
                    </td>
                    <td className="tnum px-3 py-3.5 text-right font-medium"
                        style={{ color: (s.projected.expected_success_delta ?? 0) > 0 ? "var(--success)" : "var(--muted-foreground)" }}>
                      {pctDelta(s.projected.expected_success_delta)}
                    </td>
                    <td className="tnum px-3 py-3.5 text-right text-[color:var(--projected)]">
                      {inr(s.projected.projected_gmv_retained)}
                    </td>
                    <td className="tnum px-3 py-3.5 text-right text-muted-foreground">
                      {ms(s.projected.latency_delta_ms)}
                    </td>
                    <td className="tnum px-3 py-3.5 text-right text-muted-foreground">
                      {s.projected.risk_score?.toFixed(3) ?? "—"}
                    </td>
                    <td className="px-5 py-3.5 text-right">
                      <button
                        onClick={() => setSelected(s.simulation_id === selected ? null : s.simulation_id)}
                        aria-expanded={s.simulation_id === selected}
                        className="text-[12px] text-[color:var(--accent)] hover:underline"
                      >
                        {s.simulation_id === selected ? "Hide" : "Inspect"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {candidates.some((s) => s.status !== "VALID") && (
          <p className="border-t border-border px-5 py-3 text-[12px] leading-relaxed text-faint-foreground">
            Invalid candidates are kept visible with their reason. A candidate the engine
            refused is evidence about the search, not something to hide.
          </p>
        )}
      </Panel>

      {detail && <SimulationDetail sim={detail} />}

      <div className="flex items-center justify-between gap-4">
        <Button variant="secondary" onClick={() => void analyze.run()} disabled={analyze.pending}>
          {analyze.pending ? "Re-running…" : "Re-run deterministic analysis"}
        </Button>
        <button
          onClick={() => onGo("recommendation")}
          className="text-[13px] font-medium text-[color:var(--accent)] hover:underline"
        >
          Continue to recommendation →
        </button>
      </div>
      {analyze.error && <ErrorState error={analyze.error} compact />}
    </div>
  );
}

/** Full provenance for one candidate: identity, what was held fixed, and the caveats. */
function SimulationDetail({ sim }: { sim: Sim }) {
  return (
    <Panel kicker={`Simulation ${sim.simulation_id}`} title={sim.candidate_key} right={<TruthTag truth="SIMULATED" />}>
      {sim.invalid_reason && (
        <div className="mb-4 rounded-md border border-[color:var(--warning)]/30 bg-[color:var(--warning)]/[0.06] px-3 py-2">
          <span className="text-[12px] text-muted-foreground">{sim.invalid_reason}</span>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div>
          <Kicker>Identity &amp; reproducibility</Kicker>
          <div className="mt-2 space-y-0.5">
            <MonoField label="Seed" value={sim.identity.simulation_seed ?? "UNAVAILABLE"} />
            <MonoField label="Input fingerprint" value={fp(sim.identity.input_fingerprint, 20)} />
            <MonoField label="Simulation fingerprint" value={fp(sim.identity.simulation_fingerprint, 20)} />
            <MonoField label="Model version" value={sim.identity.model_version ?? "UNAVAILABLE"} />
            <MonoField label="Policy version" value={sim.identity.policy_version ?? "UNAVAILABLE"} />
            <MonoField label="Profile version" value={sim.identity.profile_version ?? "UNAVAILABLE"} />
            <MonoField
              label="Capacity"
              value={typeof sim.capacity_utilization === "string" ? sim.capacity_utilization : String(sim.capacity_utilization)}
            />
          </div>
        </div>
        <div>
          <Kicker>Held constant vs changed</Kicker>
          <div className="mt-2 space-y-2">
            <KeyValues title="Held constant" data={sim.held_constant} tone="muted" />
            <KeyValues title="Changed" data={sim.changed_variables} tone="accent" />
          </div>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <KeyValues title="Assumptions" data={sim.assumptions} tone="muted" />
        <KeyValues title="Limitations" data={sim.limitations} tone="warning" />
      </div>
    </Panel>
  );
}

function KeyValues({
  title,
  data,
  tone,
}: {
  title: string;
  data: Record<string, unknown> | null;
  tone: "muted" | "accent" | "warning";
}) {
  const color =
    tone === "accent" ? "var(--accent)" : tone === "warning" ? "var(--warning)" : "var(--muted-foreground)";
  const entries = Object.entries(data ?? {});
  return (
    <div className="rounded-md border border-border bg-surface-0 p-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.1em]" style={{ color }}>
        {title}
      </div>
      {entries.length === 0 ? (
        <p className="mt-1.5 font-mono text-[11px] text-faint-foreground">UNAVAILABLE</p>
      ) : (
        <dl className="mt-2 space-y-1.5">
          {entries.map(([k, v]) => (
            <div key={k} className="flex items-start justify-between gap-3">
              <dt className="text-[11px] text-muted-foreground">{k.replace(/_/g, " ")}</dt>
              <dd className="tnum max-w-[60%] break-words text-right font-mono text-[11px] text-foreground">
                {typeof v === "object" ? JSON.stringify(v) : String(v)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function Fig({ label, value, tone }: { label: string; value: string; tone?: "critical" }) {
  return (
    <div>
      <div className="text-[10px] font-medium uppercase tracking-[0.09em] text-muted-foreground">{label}</div>
      <div
        className="tnum mt-1 font-display text-lg font-semibold"
        style={{ color: tone === "critical" ? "var(--critical)" : "var(--foreground)" }}
      >
        {value}
      </div>
    </div>
  );
}
