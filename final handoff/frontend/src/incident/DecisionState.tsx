import type { IncidentTab } from "./IncidentWorkspace";
import { cx, Kicker } from "../components/ui";
import { stepProgress, isStopped, recoveryLabel } from "../lib/recovery";
import type { RecoveryState } from "../lib/types";

/**
 * The authority-model tracker: AI proposes → policy decides → human approves →
 * system executes → verification confirms.
 *
 * Every tick here is derived from backend state via `stepProgress`. The prototype drove
 * this from local booleans, which meant a browser refresh silently rewound the workflow
 * and two operators could see different progress on the same incident.
 */
export function DecisionState({
  recovery,
  tab,
  onGo,
}: {
  recovery: RecoveryState | null;
  tab: IncidentTab;
  onGo: (t: IncidentTab) => void;
}) {
  const p = stepProgress(recovery);
  const state = recovery?.state ?? "NO_ACTIVE_ACTION";
  const stopped = isStopped(state);

  const steps: {
    key: IncidentTab;
    label: string;
    owner: string;
    done: boolean;
    current?: boolean;
  }[] = [
    { key: "evidence", label: "Diagnosed", owner: "Deterministic", done: p.diagnosed },
    { key: "simulation", label: "Simulated", owner: "Deterministic", done: p.simulated },
    { key: "recommendation", label: "Recommended", owner: "Deterministic candidate", done: p.recommended },
    { key: "recommendation", label: "Policy validated", owner: "Deterministic", done: p.policyValidated },
    {
      key: "approval",
      label: "Human approved",
      owner: "Human",
      done: p.approved,
      current: !p.approved && !stopped && p.policyValidated,
    },
    {
      key: "verification",
      label: "Executed",
      owner: "Simulated adapter",
      done: p.executed,
      current: p.approved && !p.executed && !stopped,
    },
    {
      key: "verification",
      label: "Verified",
      owner: "Deterministic",
      done: p.verified,
      current: p.executed && !p.verified,
    },
  ];

  return (
    <div className="rounded-[var(--radius)] border border-border bg-surface-1 p-4">
      <Kicker>Decision State</Kicker>
      <ol className="mt-3 space-y-0">
        {steps.map((s, i) => (
          <li key={`${s.label}-${i}`} className="relative flex gap-3 pb-3.5 last:pb-0">
            {i < steps.length - 1 && (
              <span
                className={cx(
                  "absolute left-[7px] top-4 h-full w-px",
                  s.done ? "bg-[color:var(--success)]/40" : "bg-border",
                )}
                aria-hidden
              />
            )}
            <span
              className={cx(
                "z-10 mt-0.5 flex size-3.5 shrink-0 items-center justify-center rounded-full",
                s.done
                  ? "bg-[color:var(--success)]"
                  : s.current
                    ? "bg-[color:var(--accent)] live-dot"
                    : "bg-surface-3 ring-1 ring-border",
              )}
              aria-hidden
            >
              {s.done && (
                <svg width="9" height="9" viewBox="0 0 24 24" fill="none" className="text-[#04121a]">
                  <path d="m5 13 4 4L19 7" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </span>
            <button onClick={() => onGo(s.key)} className="min-w-0 flex-1 text-left">
              <div
                className={cx(
                  "text-[13px] font-medium",
                  s.done ? "text-foreground" : s.current ? "text-[color:var(--accent)]" : "text-muted-foreground",
                )}
              >
                {/* Text state, not colour alone (§35). */}
                {s.label}
                {s.done && <span className="sr-only"> — complete</span>}
                {s.current && <span className="sr-only"> — in progress</span>}
              </div>
              <div className="text-[10px] uppercase tracking-wide text-faint-foreground">{s.owner}</div>
            </button>
          </li>
        ))}
      </ol>

      {/* A deliberate stop is shown as a terminal outcome, not a stalled step. */}
      {stopped && (
        <div className="mt-3 rounded-md border border-[color:var(--warning)]/30 bg-[color:var(--warning)]/[0.06] px-3 py-2">
          <div className="text-[10px] font-semibold uppercase tracking-[0.09em] text-[color:var(--warning)]">
            Stopped
          </div>
          <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
            {recoveryLabel(state)} — {recovery?.detail}
          </p>
        </div>
      )}
    </div>
  );
}
