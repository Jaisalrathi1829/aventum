import { useState } from "react";
import { Panel, StatusPill, Kicker, Button, cx } from "../components/ui";
import { ErrorState, LoadingPanel, EmptyState } from "../components/states";
import { api } from "../lib/api";
import { useResource } from "../lib/hooks";
import type { AuditEvent } from "../lib/types";
import { fp, ts } from "../lib/format";

/**
 * The audit trail: incident → evidence → RCA → simulation → policy → recommendation →
 * approval → action → verification, reconstructed from persisted events.
 *
 * Nothing is synthesised for display. If a lifecycle stage is missing from the database
 * it is missing here too, and the completeness strip says so — a timeline that always
 * looks complete would be worthless as an audit.
 */

/** The lifecycle the flagship run must produce, in the order it must appear. */
const LIFECYCLE: { type: string; label: string }[] = [
  { type: "SIMULATION_COMPLETED", label: "Simulation" },
  { type: "POLICY_VALIDATED", label: "Policy" },
  { type: "RECOMMENDATION_CREATED", label: "Recommendation" },
  { type: "APPROVAL_REQUESTED", label: "Approval requested" },
  { type: "APPROVAL_DECIDED", label: "Human decision" },
  { type: "ACTION_EXECUTED", label: "Execution" },
  { type: "VERIFICATION_COMPLETED", label: "Verification" },
];

export function Audit({ incidentId }: { incidentId: number | null }) {
  const [showSimulations, setShowSimulations] = useState(false);
  const audit = useResource(
    () => api.audit(incidentId as number),
    [incidentId],
    { enabled: incidentId !== null },
  );

  if (incidentId === null) {
    return (
      <div className="mx-auto max-w-[1100px] px-8 py-6">
        <EmptyState title="No incident selected" detail="Open an incident to view its audit trail." />
      </div>
    );
  }
  if (audit.initialLoading) {
    return <div className="mx-auto max-w-[1100px] px-8 py-6"><LoadingPanel label="Loading audit trail" rows={6} /></div>;
  }
  if (audit.error && !audit.data) {
    return (
      <div className="mx-auto max-w-[1100px] px-8 py-6">
        <ErrorState error={audit.error} onRetry={() => void audit.refresh()} />
      </div>
    );
  }

  const events = audit.data?.events ?? [];
  const present = new Set(events.map((e) => e.event_type));
  const simulationCount = events.filter((e) => e.event_type === "SIMULATION_COMPLETED").length;
  const visible = showSimulations ? events : events.filter((e) => e.event_type !== "SIMULATION_COMPLETED");

  return (
    <div className="mx-auto max-w-[1100px] space-y-6 px-8 py-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <Kicker>Audit</Kicker>
          <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight text-foreground">
            Incident {incidentId} · {events.length} persisted events
          </h1>
        </div>
        <Button variant="secondary" onClick={() => void audit.refresh()}>Refresh</Button>
      </div>

      {/* Lifecycle completeness — computed from what exists, not asserted. */}
      <Panel kicker="Lifecycle Completeness" title="Required events for a complete run">
        <ol className="flex flex-wrap items-center gap-x-1 gap-y-3">
          {LIFECYCLE.map((stage, i) => {
            const ok = present.has(stage.type);
            return (
              <li key={stage.type} className="flex items-center">
                <span
                  className={cx(
                    "inline-flex items-center gap-1.5 rounded-[3px] border px-2 py-1 text-[11px] font-medium",
                    ok
                      ? "border-[color:var(--success)]/30 bg-[color:var(--success)]/[0.08] text-[color:var(--success)]"
                      : "border-border bg-surface-2 text-faint-foreground",
                  )}
                >
                  <span className="size-1.5 rounded-full"
                        style={{ backgroundColor: ok ? "var(--success)" : "var(--faint-foreground)" }} aria-hidden />
                  {stage.label}
                  <span className="sr-only">{ok ? " present" : " missing"}</span>
                </span>
                {i < LIFECYCLE.length - 1 && <span className="px-1 text-faint-foreground" aria-hidden>›</span>}
              </li>
            );
          })}
        </ol>
        {LIFECYCLE.some((s) => !present.has(s.type)) && (
          <p className="mt-4 text-[12px] leading-relaxed text-muted-foreground">
            This run has not reached every lifecycle stage. Missing stages are shown greyed
            rather than hidden, because an audit that only ever looks complete cannot be used
            to find out that something did not happen.
          </p>
        )}
      </Panel>

      <Panel
        kicker="Event Chain"
        title={`${visible.length} shown`}
        right={
          simulationCount > 0 ? (
            <button
              onClick={() => setShowSimulations((s) => !s)}
              aria-expanded={showSimulations}
              className="text-[12px] text-muted-foreground transition-colors hover:text-foreground"
            >
              {showSimulations ? "Hide" : "Show"} {simulationCount} simulation events
            </button>
          ) : undefined
        }
        flush
      >
        {visible.length === 0 ? (
          <EmptyState
            title="No audit events"
            detail="Nothing has been recorded for this incident yet."
          />
        ) : (
          <ul className="divide-y divide-border">
            {visible.map((e) => <EventRow key={e.event_id} event={e} />)}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function EventRow({ event }: { event: AuditEvent }) {
  const [open, setOpen] = useState(false);
  const isHuman = event.actor.startsWith("HUMAN:");
  const ref = event.output_ref ?? event.input_ref ?? null;

  return (
    <li className="px-5 py-3.5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <span className="tnum mt-0.5 shrink-0 font-mono text-[11px] text-faint-foreground">
            #{event.event_id}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-[13px] text-foreground">
                {event.event_type.replace(/_/g, " ")}
              </span>
              <StatusPill tone={isHuman ? "warning" : "muted"}>
                {isHuman ? "HUMAN" : event.actor}
              </StatusPill>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-faint-foreground">
              <span>{ts(event.occurred_at)}</span>
              {isHuman && <span className="text-[color:var(--human)]">{event.actor.slice(6)}</span>}
              {ref && <span>{JSON.stringify(ref)}</span>}
              {event.fingerprint && <span title={event.fingerprint}>fp {fp(event.fingerprint, 12)}</span>}
            </div>
          </div>
        </div>
        {event.payload && (
          <button
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="shrink-0 text-[12px] text-[color:var(--accent)] hover:underline"
          >
            {open ? "Hide" : "Payload"}
          </button>
        )}
      </div>
      {open && event.payload && (
        <pre className="mt-3 max-h-64 overflow-auto rounded-md border border-border bg-surface-0 p-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      )}
    </li>
  );
}
