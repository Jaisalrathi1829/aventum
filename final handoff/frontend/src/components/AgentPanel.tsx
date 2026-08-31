import { useEffect, useState } from "react";
import { cx, Button, Kicker, StatusPill } from "./ui";
import { ErrorState, LoadingPanel } from "./states";
import { api } from "../lib/api";
import { useMutation, type Resource } from "../lib/hooks";
import { humanize, ms } from "../lib/format";
import type { AgentRun, Recommendation } from "../lib/types";

/**
 * The embedded operational agent — NOT a chat window (§21).
 *
 * Shows what the agent actually DID: its tool calls, its status, its budget usage, and
 * the rationale it produced. It never shows chain-of-thought, and with `think:false` in
 * Day 4B none is produced to show. Everything here comes from the persisted agent run;
 * when no run exists the panel says so rather than inventing plausible activity.
 */
export function AgentPanel({
  incidentId,
  resource,
  reachable,
  recommendation,
}: {
  incidentId: number;
  resource: Resource<{ agent_run: AgentRun | null; detail?: string }>;
  reachable: boolean;
  recommendation: Recommendation | null;
}) {
  const [showRationale, setShowRationale] = useState(false);
  const run = resource.data?.agent_run ?? null;

  const runAgent = useMutation(async () => {
    const result = await api.runAgent(incidentId);
    await resource.refresh();
    return result;
  });

  const statusPill = !reachable ? (
    <StatusPill tone="critical" dot>Unavailable</StatusPill>
  ) : runAgent.pending ? (
    <StatusPill tone="accent" dot>Running</StatusPill>
  ) : run ? (
    <StatusPill tone={run.status === "SUCCEEDED" ? "success" : run.status === "ABANDONED" ? "critical" : "warning"} dot>
      {humanize(run.status)}
    </StatusPill>
  ) : (
    <StatusPill tone="muted">Not run</StatusPill>
  );

  return (
    <div className="rounded-[var(--radius)] border border-border bg-surface-1">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="flex items-center gap-2.5">
          <div className="flex size-6 items-center justify-center rounded-[5px] border border-border bg-surface-2">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" style={{ color: "var(--agent)" }} aria-hidden>
              <circle cx="12" cy="12" r="3.2" stroke="currentColor" strokeWidth="1.6" />
              <path d="M12 3v3m0 12v3M3 12h3m12 0h3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </div>
          <div>
            <div className="font-display text-[13px] font-semibold text-foreground">Aventum Agent</div>
            <div className="text-[9px] uppercase tracking-[0.11em] text-faint-foreground">Operational copilot</div>
          </div>
        </div>
        {statusPill}
      </div>

      {/* Agent down: say so, and say plainly what still works (§27). */}
      {!reachable ? (
        <div className="space-y-3 p-4">
          <p className="text-[13px] text-muted-foreground">
            The Aventum Agent is currently unavailable.
          </p>
          <div className="rounded-md border border-[color:var(--success)]/25 bg-[color:var(--success)]/[0.06] p-3">
            <div className="flex items-center gap-2">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" className="text-[color:var(--success)]" aria-hidden>
                <path d="m5 13 4 4L19 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span className="text-[13px] font-medium text-foreground">
                Deterministic incident analysis remains available.
              </span>
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
              Evidence, significance, simulation and policy gates are computed by deterministic
              systems and are unaffected. No explanation or recommendation is fabricated in the
              agent's absence.
            </p>
          </div>
        </div>
      ) : resource.initialLoading ? (
        <div className="p-4">
          <LoadingPanel label="Loading agent activity" rows={3} />
        </div>
      ) : (
        <div className="space-y-4 p-4">
          {resource.error && <ErrorState error={resource.error} onRetry={() => void resource.refresh()} compact />}

          {run ? (
            <>
              <div>
                <Kicker>Tool activity</Kicker>
                {run.tool_calls.length === 0 ? (
                  <p className="mt-2 text-[12px] text-faint-foreground">
                    No tools were called in this run.
                  </p>
                ) : (
                  <ul className="mt-2 space-y-1.5">
                    {run.tool_calls.map((c) => (
                      <li key={c.tool_call_id} className="flex items-center gap-2 text-[13px] text-muted-foreground">
                        <svg
                          width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden
                          className={cx("shrink-0", c.outcome === "OK" || c.outcome === "SUCCESS" ? "text-[color:var(--success)]" : "text-[color:var(--warning)]")}
                        >
                          <path d="m5 13 4 4L19 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                        <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{c.tool_name}</span>
                        <span className="tnum shrink-0 font-mono text-[10px] text-faint-foreground">
                          {ms(c.latency_ms)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="grid grid-cols-3 gap-px overflow-hidden rounded-md border border-border bg-border">
                <Budget label="Turns" value={run.turns_used} />
                <Budget label="Tools" value={run.tool_calls_used} />
                <Budget label="Sims" value={run.simulations_used} />
              </div>

              {run.error_message && (
                <div className="rounded-md border border-[color:var(--warning)]/30 bg-[color:var(--warning)]/[0.06] px-3 py-2">
                  <p className="text-[12px] leading-relaxed text-muted-foreground">{run.error_message}</p>
                </div>
              )}
            </>
          ) : (
            <div className="space-y-3">
              {runAgent.pending ? (
                <RunningNotice />
              ) : (
                <p className="text-[13px] leading-relaxed text-muted-foreground">
                  {resource.data?.detail ?? "No agent run exists for this incident."}
                </p>
              )}
              <Button variant="secondary" full onClick={() => void runAgent.run()} disabled={runAgent.pending}>
                {runAgent.pending ? "Analysing…" : "Run agent analysis"}
              </Button>
              {runAgent.error && <ErrorState error={runAgent.error} compact />}
              <p className="text-[11px] leading-relaxed text-faint-foreground">
                The agent interprets and orchestrates. It cannot compute business numbers,
                approve, or execute.
              </p>
            </div>
          )}

          {/* The rationale is a CONCLUSION, attributed to the agent, and clearly separated
              from the deterministic authority that actually permitted the action. */}
          {recommendation?.rationale && (
            <>
              <button
                onClick={() => setShowRationale((s) => !s)}
                aria-expanded={showRationale}
                className="flex w-full items-center justify-between rounded-md border border-border px-3 py-2 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
              >
                Agent rationale
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden
                     className={cx("transition-transform", showRationale && "rotate-180")}>
                  <path d="m6 9 6 6 6-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
              {showRationale && (
                <div className="rounded-md border border-[color:var(--agent)]/25 bg-[color:var(--agent)]/[0.05] p-3">
                  <div className="mb-2 text-[10px] uppercase tracking-wide" style={{ color: "var(--agent)" }}>
                    AI-generated explanation
                  </div>
                  <p className="text-[13px] leading-relaxed text-muted-foreground">
                    {recommendation.rationale}
                  </p>
                  <p className="mt-2 font-mono text-[11px] leading-relaxed text-faint-foreground">
                    Conclusion only. Chain-of-thought is neither produced nor stored.
                  </p>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * In-progress state for a genuinely slow operation.
 *
 * A local 8B model takes roughly a minute to work through a multi-turn loop against a
 * real database. Without a visible clock and a stated expectation, that minute is
 * indistinguishable from a hang -- which is exactly how it was first read.
 *
 * The elapsed counter is real; nothing here is a fake progress bar.
 */
function RunningNotice() {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div
      className="rounded-md border border-[color:var(--accent)]/30 bg-[color:var(--accent)]/[0.06] px-3 py-2.5"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-[12px] font-medium text-[color:var(--accent)]">
          <span className="live-dot size-1.5 rounded-full bg-[color:var(--accent)]" aria-hidden />
          Agent analysing
        </span>
        <span className="tnum font-mono text-[11px] text-muted-foreground">{seconds}s</span>
      </div>
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
        Typically 45–90 seconds on a local 8B model. The deterministic analysis on this
        page is already complete and is not waiting on this.
      </p>
    </div>
  );
}

function Budget({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="bg-surface-1 px-2 py-2 text-center">
      <div className="tnum font-display text-[15px] font-semibold text-foreground">{value ?? "—"}</div>
      <div className="text-[9px] uppercase tracking-wide text-faint-foreground">{label}</div>
    </div>
  );
}
