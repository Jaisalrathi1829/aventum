import { Component, type ErrorInfo, type ReactNode } from "react";
import { ApiError } from "../lib/api";
import { Button, Panel, cx } from "./ui";

// ============================================================
// Loading, empty, error and stop states.
//
// One implementation each, so a failed call can never render as a calm green panel
// (§29) and an empty result can never be mistaken for a working one.
// ============================================================

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cx("animate-pulse rounded-md bg-surface-2", className)}
      aria-hidden="true"
    />
  );
}

export function LoadingPanel({ label = "Loading", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="space-y-3" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className={cx("h-4", i === 0 ? "w-1/3" : i === rows - 1 ? "w-2/3" : "w-1/2")} />
      ))}
    </div>
  );
}

/**
 * An API failure, stated plainly.
 *
 * Shows the backend's stable code so an operator can report it, the human sentence, and
 * a retry only when retrying could actually help — offering "Retry" on a 409 conflict
 * teaches people to click through refusals.
 */
export function ErrorState({
  error,
  onRetry,
  compact,
}: {
  error: ApiError;
  onRetry?: () => void;
  compact?: boolean;
}) {
  return (
    <div
      role="alert"
      className={cx(
        "rounded-md border border-[color:var(--critical)]/35 bg-[color:var(--critical)]/[0.06]",
        compact ? "px-3 py-2.5" : "px-4 py-4",
      )}
    >
      <div className="flex items-start gap-3">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden
             className="mt-0.5 shrink-0 text-[color:var(--critical)]">
          <path d="M12 2 1 21h22L12 2Zm0 5 7.5 13h-15L12 7Zm-1 4v4h2v-4h-2Zm0 5v2h2v-2h-2Z" />
        </svg>
        <div className="min-w-0 flex-1">
          <div className="font-medium text-[13px] text-foreground">{error.message}</div>
          <div className="mt-1 font-mono text-[11px] text-faint-foreground">{error.code}</div>
          {onRetry && error.retryable && (
            <Button variant="secondary" onClick={onRetry} className="mt-3">
              Retry
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-10 text-center">
      <div className="font-display text-[14px] font-medium text-muted-foreground">{title}</div>
      {detail && <p className="max-w-md text-[12px] leading-relaxed text-faint-foreground">{detail}</p>}
    </div>
  );
}

/**
 * A deliberate stop (§20): the system declined to proceed and says why.
 *
 * Visually distinct from ErrorState because these are opposite events — one is the
 * product failing, the other is the product working correctly by refusing.
 */
export function StoppedState({
  title,
  why,
  next,
  tone = "warning",
}: {
  title: string;
  why: string;
  next?: string | null;
  tone?: "warning" | "muted" | "critical";
}) {
  const color =
    tone === "critical" ? "var(--critical)" : tone === "muted" ? "var(--muted-foreground)" : "var(--warning)";
  return (
    <div
      className="rounded-md border px-4 py-3.5"
      style={{
        borderColor: `color-mix(in srgb, ${color} 32%, transparent)`,
        backgroundColor: `color-mix(in srgb, ${color} 6%, transparent)`,
      }}
    >
      <div className="flex items-center gap-2">
        <span className="size-1.5 rounded-full" style={{ backgroundColor: color }} aria-hidden />
        <span className="text-[11px] font-semibold uppercase tracking-[0.09em]" style={{ color }}>
          {title}
        </span>
      </div>
      <p className="mt-2 text-[13px] leading-relaxed text-foreground">{why}</p>
      {next && (
        <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">
          <span className="font-medium text-muted-foreground">Next: </span>
          {next}
        </p>
      )}
    </div>
  );
}

// --- Application error boundary ---------------------------------------
type BoundaryState = { error: Error | null };

/**
 * Catches render-time crashes so one broken panel cannot blank the whole console.
 *
 * Nothing from the error object is rendered beyond its message, and stack traces stay in
 * the browser console where a developer can find them (§29).
 */
export class ErrorBoundary extends Component<{ children: ReactNode; label?: string }, BoundaryState> {
  state: BoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Aventum render error", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="p-6">
        <Panel kicker="Interface error" title={this.props.label ?? "This view could not be rendered"}>
          <p className="text-[13px] leading-relaxed text-muted-foreground">
            Something in this view failed to render. Backend state is unaffected — no action was
            taken and nothing was changed.
          </p>
          <div className="mt-3 font-mono text-[11px] text-faint-foreground">
            {this.state.error.message}
          </div>
          <Button variant="secondary" className="mt-4" onClick={() => this.setState({ error: null })}>
            Try again
          </Button>
        </Panel>
      </div>
    );
  }
}
