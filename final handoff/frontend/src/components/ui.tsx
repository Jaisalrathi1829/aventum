import type { ReactNode } from "react";
import type { Truth, Severity } from "../lib/types";

// ============================================================
// Aventum design system — primitives
// ============================================================

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

// --- Truth / provenance label -----------------------------------------
const truthStyles: Record<Truth, { color: string; label: string }> = {
  OBSERVED: { color: "var(--observed)", label: "OBSERVED" },
  SYNTHETIC: { color: "var(--synthetic)", label: "SYNTHETIC" },
  SIMULATED: { color: "var(--simulated)", label: "SIMULATED" },
  PROJECTED: { color: "var(--projected)", label: "PROJECTED" },
  VERIFIED: { color: "var(--verified)", label: "VERIFIED" },
  DETERMINISTIC: { color: "var(--deterministic)", label: "DETERMINISTIC" },
  HUMAN: { color: "var(--human)", label: "HUMAN" },
  AGENT: { color: "var(--agent)", label: "AI-GENERATED" },
};

export function TruthTag({ truth, className }: { truth: Truth; className?: string }) {
  const s = truthStyles[truth];
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-[3px] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.07em]",
        className,
      )}
      style={{ color: s.color, backgroundColor: `color-mix(in srgb, ${s.color} 11%, transparent)`, border: `1px solid color-mix(in srgb, ${s.color} 24%, transparent)` }}
    >
      <span className="size-1 rounded-full" style={{ backgroundColor: s.color }} />
      {s.label}
    </span>
  );
}

// --- Severity badge ----------------------------------------------------
const sevColor: Record<Severity, string> = {
  CRITICAL: "var(--critical)",
  HIGH: "var(--warning)",
  MEDIUM: "var(--warning)",
  LOW: "var(--muted-foreground)",
  // The backend emits NONE for an incident whose evidence does not support acting.
  // It is a real severity, not a missing one, and it reads as calm rather than absent.
  NONE: "var(--faint-foreground)",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  const c = sevColor[severity];
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-[3px] px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[0.09em]"
      style={{ color: c, backgroundColor: `color-mix(in srgb, ${c} 13%, transparent)`, border: `1px solid color-mix(in srgb, ${c} 28%, transparent)` }}
    >
      <span className="size-1.5 rounded-full" style={{ backgroundColor: c }} />
      {severity}
    </span>
  );
}

// --- Generic status pill ----------------------------------------------
type Tone = "neutral" | "accent" | "success" | "warning" | "critical" | "muted";
const toneColor: Record<Tone, string> = {
  neutral: "var(--foreground)",
  accent: "var(--accent)",
  success: "var(--success)",
  warning: "var(--warning)",
  critical: "var(--critical)",
  muted: "var(--muted-foreground)",
};

export function StatusPill({ children, tone = "neutral", dot, className }: { children: ReactNode; tone?: Tone; dot?: boolean; className?: string }) {
  const c = toneColor[tone];
  return (
    <span
      className={cx("inline-flex items-center gap-1.5 rounded-[3px] px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[0.06em]", className)}
      style={{ color: c, backgroundColor: `color-mix(in srgb, ${c} 11%, transparent)`, border: `1px solid color-mix(in srgb, ${c} 22%, transparent)` }}
    >
      {dot && <span className="size-1.5 rounded-full" style={{ backgroundColor: c }} />}
      {children}
    </span>
  );
}

// --- Panel -------------------------------------------------------------
export function Panel({ children, className, title, kicker, right, flush }: { children: ReactNode; className?: string; title?: ReactNode; kicker?: string; right?: ReactNode; flush?: boolean }) {
  return (
    <section className={cx("rounded-[var(--radius)] border border-border bg-surface-1", className)}>
      {(title || kicker || right) && (
        <header className="flex items-center justify-between gap-4 border-b border-border px-5 py-3.5">
          <div className="min-w-0">
            {kicker && <div className="text-[10px] font-semibold uppercase tracking-[0.13em] text-faint-foreground">{kicker}</div>}
            {title && <h3 className="truncate font-display text-[15px] font-semibold text-foreground">{title}</h3>}
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </header>
      )}
      <div className={cx(!flush && "p-5")}>{children}</div>
    </section>
  );
}

// --- Metric card -------------------------------------------------------
export function Metric({ label, value, unit, truth, delta, deltaBad, sub, big }: { label: string; value: string; unit?: string; truth?: Truth; delta?: string | null; deltaBad?: boolean; sub?: ReactNode; big?: boolean }) {
  const unavailable = value === "UNAVAILABLE";
  return (
    <div className="flex flex-col gap-1.5">
      {/* Wraps rather than truncates. In a six-across metric strip the column is far
          narrower than "SIGNIFICANCE" plus a "DETERMINISTIC" tag, and a half-rendered
          "DETERMINIS…" is worse than a second line: the provenance label is the part a
          reader has to be able to trust at a glance. */}
      <div className="flex flex-wrap items-start justify-between gap-x-2 gap-y-1">
        <span className="text-[11px] font-medium uppercase leading-tight tracking-[0.09em] text-muted-foreground">{label}</span>
        {truth && <TruthTag truth={truth} className="shrink-0" />}
      </div>
      <div className="flex items-baseline gap-1.5">
        {unavailable ? (
          <span className="font-mono text-sm font-medium tracking-wide text-faint-foreground">UNAVAILABLE</span>
        ) : (
          <>
            <span className={cx("tnum font-display font-semibold text-foreground", big ? "text-[30px] leading-none" : "text-[22px] leading-none")}>{value}</span>
            {unit && <span className="text-sm font-medium text-muted-foreground">{unit}</span>}
          </>
        )}
        {delta && (
          <span className="tnum ml-1 font-mono text-xs font-medium" style={{ color: deltaBad ? "var(--critical)" : "var(--success)" }}>{delta}</span>
        )}
      </div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

// --- Before → After comparison ----------------------------------------
export function BeforeAfter({ label, before, after, good, truthBefore = "OBSERVED", truthAfter = "VERIFIED" }: { label: string; before: string; after: string; good?: boolean; truthBefore?: Truth; truthAfter?: Truth }) {
  const unavailable = before === "UNAVAILABLE" || after === "UNAVAILABLE";
  return (
    <div className="rounded-md border border-border bg-surface-0 p-4">
      <div className="mb-3 text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">{label}</div>
      {unavailable ? (
        <span className="font-mono text-sm text-faint-foreground">UNAVAILABLE</span>
      ) : (
        <div className="flex items-center gap-3">
          <div className="flex flex-col gap-1">
            <span className="tnum font-display text-xl font-semibold text-muted-foreground">{before}</span>
            <TruthTag truth={truthBefore} />
          </div>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" className="shrink-0 text-faint-foreground" aria-hidden><path d="M5 12h14m0 0-5-5m5 5-5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <div className="flex flex-col gap-1">
            <span className="tnum font-display text-xl font-semibold" style={{ color: good ? "var(--verified)" : "var(--foreground)" }}>{after}</span>
            <TruthTag truth={truthAfter} />
          </div>
        </div>
      )}
    </div>
  );
}

// --- Fingerprint / mono field -----------------------------------------
export function MonoField({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <span className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground">{label}</span>
      <span className={cx("tnum font-mono text-xs", value === "UNAVAILABLE" ? "text-faint-foreground" : "text-foreground")}>{value}</span>
    </div>
  );
}

// --- Button ------------------------------------------------------------
export function Button({ children, onClick, variant = "secondary", full, disabled, className, type = "button" }: { children: ReactNode; onClick?: () => void; variant?: "primary" | "secondary" | "ghost" | "danger"; full?: boolean; disabled?: boolean; className?: string; type?: "button" | "submit" }) {
  const base = "inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40";
  const styles: Record<string, string> = {
    primary: "bg-accent text-white hover:bg-[color:var(--info)] font-semibold",
    secondary: "border border-border-strong bg-surface-2 text-foreground hover:bg-surface-3",
    ghost: "text-muted-foreground hover:text-foreground hover:bg-surface-2",
    danger: "border border-[color:var(--critical)]/40 text-[color:var(--critical)] hover:bg-[color:var(--critical)]/10",
  };
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={cx(base, styles[variant], full && "w-full", className)}>
      {children}
    </button>
  );
}

// --- Small progress meter ---------------------------------------------
export function Meter({ value, tone = "accent" }: { value: number; tone?: Tone }) {
  const c = toneColor[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
      <div className="h-full rounded-full" style={{ width: `${Math.round(value * 100)}%`, backgroundColor: c }} />
    </div>
  );
}

// --- Section kicker ----------------------------------------------------
export function Kicker({ children }: { children: ReactNode }) {
  return <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-faint-foreground">{children}</div>;
}
