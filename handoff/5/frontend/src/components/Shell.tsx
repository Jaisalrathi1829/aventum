import type { ReactNode } from "react";
import { cx, StatusPill } from "./ui";
import type { EnvironmentNotice, Health } from "../lib/types";

export type Nav = "overview" | "incident" | "audit";

// --- Wordmark ----------------------------------------------------------
function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      <svg width="26" height="26" viewBox="0 0 32 32" fill="none" aria-hidden>
        <rect x="1" y="1" width="30" height="30" rx="7" stroke="var(--accent)" strokeWidth="1.5" />
        <path d="M16 7 8 25h3.4l1.5-3.6h6.2L20.6 25H24L16 7Zm-2 11.4L16 12l2 6.4h-4Z" fill="var(--accent)" />
      </svg>
      <div className="leading-none">
        <div className="font-display text-[15px] font-semibold tracking-tight text-foreground">Aventum</div>
        <div className="mt-0.5 text-[9px] uppercase tracking-[0.16em] text-faint-foreground">Payment Operations</div>
      </div>
    </div>
  );
}

const navItems: { key: Nav; label: string; icon: ReactNode }[] = [
  { key: "overview", label: "Overview", icon: <path d="M4 13h6V4H4v9Zm0 7h6v-5H4v5Zm8 0h6V11h-6v9Zm0-16v5h6V4h-6Z" /> },
  { key: "incident", label: "Incidents", icon: <path d="M12 3 2 20h20L12 3Zm0 4.5L18.5 18h-13L12 7.5ZM11 11v4h2v-4h-2Zm0 5v2h2v-2h-2Z" /> },
  { key: "audit", label: "Audit", icon: <path d="M4 4h11l5 5v11H4V4Zm2 2v12h12V10h-4V6H6Zm2 8h8v2H8v-2Zm0-3h8v2H8v-2Z" /> },
];

export function Sidebar({ nav, onNav, incidentCount, health, healthFailed }: { nav: Nav; onNav: (n: Nav) => void; incidentCount: number; health: Health | null; healthFailed?: boolean }) {
  return (
    <aside className="flex w-[236px] shrink-0 flex-col border-r border-border bg-surface-0">
      <div className="flex h-14 items-center border-b border-border px-5">
        <Wordmark />
      </div>
      <nav className="flex flex-col gap-0.5 p-3">
        {navItems.map((item) => {
          const active = nav === item.key;
          return (
            <button
              key={item.key}
              onClick={() => onNav(item.key)}
              className={cx(
                "group flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active ? "bg-surface-2 text-foreground" : "text-muted-foreground hover:bg-surface-1 hover:text-foreground",
              )}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" className={cx(active ? "text-accent" : "text-faint-foreground group-hover:text-muted-foreground")} fill="currentColor" aria-hidden>
                {item.icon}
              </svg>
              <span className="flex-1 text-left">{item.label}</span>
              {item.key === "incident" && incidentCount > 0 && (
                <span className="tnum rounded-[3px] bg-[color:var(--critical)]/15 px-1.5 text-[10px] font-semibold text-[color:var(--critical)]">{incidentCount}</span>
              )}
            </button>
          );
        })}
      </nav>

      <div className="mt-auto space-y-3 border-t border-border p-4">
        <div className="text-[10px] font-semibold uppercase tracking-[0.13em] text-faint-foreground">System Health</div>
        <div className="space-y-1.5">
          {/* A failed health call is NOT the same as a pending one. Leaving these on
              "CHECKING" while the backend is unreachable would understate an outage as a
              slow load, which is the opposite of what an operations console owes its
              reader. */}
          <HealthRow
            label="API"
            ok={healthFailed ? false : (health?.api.ok ?? null)}
            detail={healthFailed ? "unreachable" : health?.api.version}
          />
          <HealthRow
            label="Database"
            ok={healthFailed ? false : (health?.database.ok ?? null)}
            detail={healthFailed ? "unknown" : health?.database.detail}
          />
          {/* The agent is reported honestly and is NOT required: deterministic analysis
              continues without it, so a red dot here is information, not an outage. */}
          <HealthRow
            label="Agent"
            ok={healthFailed ? false : (health?.agent.ok ?? null)}
            detail={healthFailed ? "unknown" : health?.agent.detail}
            optional
          />
        </div>
      </div>
    </aside>
  );
}

function HealthRow({
  label,
  ok,
  detail,
  optional,
}: {
  label: string;
  ok: boolean | null;
  detail?: string;
  optional?: boolean;
}) {
  // Unknown (still loading) is its own state -- never silently rendered as healthy.
  const color =
    ok === null
      ? "var(--faint-foreground)"
      : ok
        ? "var(--success)"
        : optional
          ? "var(--warning)"
          : "var(--critical)";
  const text = ok === null ? "CHECKING" : (detail ?? (ok ? "OK" : "DOWN")).toUpperCase();
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[12px] text-muted-foreground">{label}</span>
      <span
        className="inline-flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide"
        style={{ color }}
      >
        {/* Shape as well as colour, so state is not communicated by hue alone (§35). */}
        <span
          className="size-1.5"
          style={{ backgroundColor: color, borderRadius: ok === false ? 1 : 999 }}
          aria-hidden
        />
        {text}
      </span>
    </div>
  );
}

export function TopBar({
  clock,
  environment,
}: {
  clock: string;
  environment: EnvironmentNotice | null;
}) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border bg-surface-0 px-6">
      <StatusPill tone="warning" dot className="!py-1">
        {environment?.mode ?? "SIMULATION MODE"}
      </StatusPill>
      <span className="hidden text-xs text-muted-foreground lg:inline">
        {environment?.detail ?? "Synthetic infrastructure · Simulated execution · No live routing changes"}
      </span>

      <div className="ml-auto flex items-center gap-5">
        <span className="tnum font-mono text-xs text-muted-foreground">{clock} IST</span>
        <div className="h-5 w-px bg-border" />
        <div className="flex items-center gap-2.5">
          <div className="flex size-7 items-center justify-center rounded-full bg-surface-3 text-[11px] font-semibold text-foreground">JS</div>
          <div className="leading-tight">
            <div className="text-xs font-medium text-foreground">jaisal</div>
            <div className="text-[10px] text-faint-foreground">Payments Ops</div>
          </div>
        </div>
      </div>
    </header>
  );
}

export function SimBanner({ environment }: { environment: EnvironmentNotice | null }) {
  return (
    <div className="flex items-center gap-3 border-b border-border bg-surface-1 px-6 py-2">
      <span className="inline-flex items-center gap-2 rounded-[3px] border border-[color:var(--warning)]/30 bg-[color:var(--warning)]/[0.08] px-2 py-0.5">
        <span className="size-1.5 rounded-full bg-[color:var(--warning)]" />
        <span className="text-[10px] font-semibold uppercase tracking-[0.09em] text-[color:var(--warning)]">
          {environment?.mode ?? "Simulation Mode"}
        </span>
      </span>
      <span className="text-xs text-muted-foreground">
        {environment?.detail ?? "Synthetic infrastructure · Simulated execution · No live routing changes"}. Outcomes are modeled, not production results.
      </span>
    </div>
  );
}
