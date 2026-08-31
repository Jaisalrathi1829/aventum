import { Panel, Metric, SeverityBadge, StatusPill, Button, Kicker, TruthTag } from "../components/ui";
import { ErrorState, LoadingPanel, EmptyState } from "../components/states";
import type { Resource } from "../lib/hooks";
import type { BatchRecovery, IncidentSummary, Overview as OverviewData, RecoveryState, Severity } from "../lib/types";
import { inr, num, pct, sigma } from "../lib/format";
import { recoveryTone, recoveryLabel } from "../lib/recovery";

export function Overview({
  resource,
  onOpenIncident,
}: {
  resource: Resource<OverviewData>;
  onOpenIncident: (id: number) => void;
}) {
  const { data, error, initialLoading, refresh } = resource;

  if (initialLoading) {
    return (
      <div className="mx-auto max-w-[1280px] px-8 py-6">
        <LoadingPanel label="Loading operations overview" rows={5} />
      </div>
    );
  }
  // A failed load with nothing cached must never render as an empty-but-calm dashboard.
  if (error && !data) {
    return (
      <div className="mx-auto max-w-[1280px] px-8 py-6">
        <ErrorState error={error} onRetry={() => void refresh()} />
      </div>
    );
  }
  if (!data) return null;

  const primary = data.primary_incident;

  return (
    <div className="mx-auto max-w-[1280px] space-y-6 px-8 py-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <Kicker>Operations Overview</Kicker>
          <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight text-foreground">
            Payment operations · synthetic environment
          </h1>
        </div>
        <StatusPill tone={data.active_incident_count > 0 ? "critical" : "success"} dot>
          {data.active_incident_count} active {data.active_incident_count === 1 ? "incident" : "incidents"}
        </StatusPill>
      </div>

      {/* A stale-data banner rather than a silent one: the numbers below are the last
          good read, and the operator is told so. */}
      {error && data && <ErrorState error={error} onRetry={() => void refresh()} compact />}

      {/* Headline diagnosis for the most significant incident. Every figure is the
          backend's; none is derived here. */}
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius)] border border-border bg-border md:grid-cols-3 lg:grid-cols-5">
        <div className="bg-surface-1 p-4">
          <Metric
            label="Significance"
            value={primary ? sigma(primary.significance_sigma) : "UNAVAILABLE"}
            truth="DETERMINISTIC"
          />
        </div>
        <div className="bg-surface-1 p-4">
          <Metric
            label="Confidence"
            value={primary ? pct(primary.confidence) : "UNAVAILABLE"}
            unit="%"
            truth="DETERMINISTIC"
          />
        </div>
        <div className="bg-surface-1 p-4">
          <Metric
            label="Evidence Strength"
            value={primary ? pct(primary.evidence_strength) : "UNAVAILABLE"}
            unit="%"
            truth="DETERMINISTIC"
          />
        </div>
        <div className="bg-surface-1 p-4">
          <Metric label="Active Incidents" value={String(data.active_incident_count)} truth="SYNTHETIC" />
        </div>
        <div className="bg-surface-1 p-4">
          <Metric
            label="Median Latency"
            value="UNAVAILABLE"
            truth="OBSERVED"
            sub="No latency telemetry in this dataset"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <BatchPanel batch={data.batch} />
        </div>
        <RecoveryPanel recovery={data.recovery} batch={data.batch} incident={primary} />
      </div>

      <Panel
        kicker="Incidents"
        title={`${data.incidents.length} ${data.incidents.length === 1 ? "incident" : "incidents"}`}
        flush
      >
        {data.incidents.length === 0 ? (
          <EmptyState
            title="No incidents"
            detail="No incident analysis exists in this environment yet."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Incidents ranked by statistical significance</caption>
              <thead>
                <tr className="border-b border-border text-[10px] uppercase tracking-wide text-faint-foreground">
                  <th scope="col" className="px-5 py-2.5 text-left font-medium">Incident</th>
                  <th scope="col" className="px-3 py-2.5 text-left font-medium">Severity</th>
                  <th scope="col" className="px-3 py-2.5 text-left font-medium">Affected Surface</th>
                  <th scope="col" className="px-3 py-2.5 text-left font-medium">Root Cause</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-medium">Confidence</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-medium">Significance</th>
                  <th scope="col" className="px-3 py-2.5 text-left font-medium">Status</th>
                  <th scope="col" className="px-5 py-2.5 text-right font-medium" />
                </tr>
              </thead>
              <tbody>
                {data.incidents.map((inc) => (
                  <IncidentRow key={inc.incident_id} incident={inc} onOpen={() => onOpenIncident(inc.incident_id)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}

function IncidentRow({ incident, onOpen }: { incident: IncidentSummary; onOpen: () => void }) {
  const severity = (incident.severity ?? "LOW") as Severity;
  return (
    <tr className="border-b border-border transition-colors last:border-0 hover:bg-surface-2">
      <td className="px-5 py-4">
        <div className="font-medium text-foreground">{incident.incident_name}</div>
        <div className="font-mono text-[11px] text-faint-foreground">
          INC-{String(incident.incident_id).padStart(4, "0")} · {incident.incident_type}
        </div>
      </td>
      <td className="px-3 py-4">
        {incident.severity ? <SeverityBadge severity={severity} /> : <span className="font-mono text-[11px] text-faint-foreground">UNAVAILABLE</span>}
      </td>
      <td className="px-3 py-4 font-mono text-[13px] text-[color:var(--accent)]">
        {incident.affected_gateway_id ?? <span className="text-faint-foreground">systemic</span>}
      </td>
      <td className="max-w-[260px] px-3 py-4">
        <div className="truncate text-muted-foreground" title={incident.predicted_root_cause ?? undefined}>
          {incident.predicted_root_cause ?? "UNAVAILABLE"}
        </div>
      </td>
      <td className="tnum px-3 py-4 text-right text-foreground">{pct(incident.confidence)}%</td>
      <td className="tnum px-3 py-4 text-right font-semibold text-[color:var(--critical)]">
        {sigma(incident.significance_sigma)}
      </td>
      <td className="px-3 py-4">
        <StatusPill tone="muted">{incident.status}</StatusPill>
      </td>
      <td className="px-5 py-4 text-right">
        <Button variant="primary" onClick={onOpen}>
          Open Incident →
        </Button>
      </td>
    </tr>
  );
}

/** Where the most significant incident stands right now. Derived server-side. */
function RecoveryPanel({
  recovery,
  batch,
  incident,
}: {
  recovery: RecoveryState;
  batch: BatchRecovery;
  incident: IncidentSummary | null;
}) {
  return (
    <Panel
      kicker={
        incident
          ? `Recovery Status · INC-${String(incident.incident_id).padStart(4, "0")}`
          : "Recovery Status"
      }
      title={recoveryLabel(recovery.state)}
    >
      <StatusPill tone={recoveryTone(recovery.state)} dot>
        {recovery.state.replace(/_/g, " ")}
      </StatusPill>
      <p className="mt-3 text-[13px] leading-relaxed text-muted-foreground">{recovery.detail}</p>
      {incident && (
        <p className="mt-2 text-[11px] leading-relaxed text-faint-foreground">
          Highest-significance incident. The counts below span every incident.
        </p>
      )}

      <div className="mt-4 space-y-2.5 border-t border-border pt-4">
        <Row label="Interventions proposed" value={num(batch.interventions_proposed)} />
        <Row label="Awaiting approval" value={num(batch.approvals_requested - batch.approvals_granted - batch.approvals_rejected - batch.approvals_expired)} />
        <Row label="Executed" value={num(batch.interventions_executed)} />
        <Row label="Verified" value={num(batch.interventions_verified)} />
      </div>

      <div className="mt-4 rounded-md border border-[color:var(--warning)]/25 bg-[color:var(--warning)]/[0.05] px-3 py-2.5">
        <p className="text-[12px] leading-relaxed text-muted-foreground">
          No action executes without a recorded human approval. The agent cannot approve, and
          neither can this interface.
        </p>
      </div>
    </Panel>
  );
}

/**
 * Batch recovery measurement (§19).
 *
 * The two money figures sit side by side and are never added: one is what simulations
 * PROJECTED across recommendations, the other is what verification MEASURED across
 * executed actions. Their ratio is the uplift, which is the honest question.
 */
function BatchPanel({ batch }: { batch: BatchRecovery }) {
  // "Nothing has been proposed yet" and "we projected zero would be retained" are
  // different claims, and ₹0.00 reads as the second. Both money figures therefore
  // report UNAVAILABLE until their own population is non-empty (§19).
  const proposedNothing = batch.interventions_proposed === 0;
  const verifiedNothing = batch.interventions_verified === 0;
  return (
    <Panel
      kicker="Batch Recovery Measurement"
      title="Population-level outcome"
      right={<TruthTag truth="VERIFIED" />}
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="rounded-md border border-border bg-surface-0 p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-medium uppercase tracking-[0.09em] text-muted-foreground">
              Projected GMV retained
            </span>
            <TruthTag truth="PROJECTED" />
          </div>
          <div className="tnum mt-2 font-display text-[26px] font-semibold leading-none text-[color:var(--projected)]">
            {proposedNothing ? "UNAVAILABLE" : inr(batch.total_projected_gmv_retained)}
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-faint-foreground">
            {proposedNothing
              ? "No intervention has been proposed yet."
              : "What simulations projected across all recommendations, before any action."}
          </p>
        </div>
        <div className="rounded-md border border-border bg-surface-0 p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-medium uppercase tracking-[0.09em] text-muted-foreground">
              Actual simulated GMV recovered
            </span>
            <TruthTag truth="VERIFIED" />
          </div>
          <div className="tnum mt-2 font-display text-[26px] font-semibold leading-none text-[color:var(--verified)]">
            {verifiedNothing ? "UNAVAILABLE" : inr(batch.total_actual_gmv_recovered)}
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-faint-foreground">
            {verifiedNothing
              ? "No action has been verified yet."
              : "Measured by independent verification. Not production money recovered."}
          </p>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-4">
        <Cell label="Incidents evaluated" value={num(batch.incidents_evaluated)} />
        <Cell label="NO_ACTION" value={num(batch.no_action_count)} hint="Correct stops" />
        <Cell label="Policy blocked" value={num(batch.policy_blocked_count)} hint="Refused by gates" />
        <Cell
          label="Recovery uplift"
          value={typeof batch.recovery_uplift === "number" ? `${(batch.recovery_uplift * 100).toFixed(1)}%` : "UNAVAILABLE"}
          hint="Measured ÷ projected"
        />
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-faint-foreground">{batch.recovery_claim}</p>
    </Panel>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-[13px] text-muted-foreground">{label}</span>
      <span className="tnum font-display text-lg font-semibold text-foreground">{value}</span>
    </div>
  );
}

function Cell({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="bg-surface-1 p-3">
      <div className="text-[10px] font-medium uppercase tracking-[0.09em] text-muted-foreground">{label}</div>
      <div className="tnum mt-1 font-display text-lg font-semibold text-foreground">{value}</div>
      {hint && <div className="mt-0.5 text-[10px] text-faint-foreground">{hint}</div>}
    </div>
  );
}
