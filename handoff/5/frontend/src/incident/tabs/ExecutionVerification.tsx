import { Panel, Button, StatusPill, TruthTag, MonoField, BeforeAfter, Kicker } from "../../components/ui";
import { ErrorState, LoadingPanel, EmptyState, StoppedState } from "../../components/states";
import { api } from "../../lib/api";
import { useMutation, type Resource } from "../../lib/hooks";
import type { RecommendationBundle, Verification } from "../../lib/types";
import { fp, inr, num, pct, pctDelta, ts } from "../../lib/format";

/**
 * Execution and independent verification.
 *
 * The governing distinction of this screen is EXECUTED ≠ VERIFIED (§49). An executed
 * action has happened; a verified one has been independently measured and may have been
 * measured to have failed. They are rendered as two separate sections with two separate
 * verdicts, and `RECOVERY_NOT_VERIFIED` is displayed as prominently as success.
 */
export function ExecutionVerification({
  bundle,
  onChanged,
  onOpenAudit,
}: {
  bundle: Resource<RecommendationBundle>;
  onChanged: () => Promise<void>;
  onOpenAudit: () => void;
}) {
  const execute = useMutation(async (recommendationId: number, approver: string) => {
    const r = await api.execute(recommendationId, approver);
    await onChanged();
    return r;
  });

  const verify = useMutation(async (actionId: number) => {
    const r = await api.verify(actionId);
    await onChanged();
    return r;
  });

  if (bundle.initialLoading) return <LoadingPanel label="Loading execution state" rows={5} />;
  if (bundle.error && !bundle.data) {
    return <ErrorState error={bundle.error} onRetry={() => void bundle.refresh()} />;
  }

  const rec = bundle.data?.recommendation ?? null;
  const approval = bundle.data?.approval ?? null;
  const action = bundle.data?.action ?? null;
  const verification = bundle.data?.verification ?? null;

  if (!rec) {
    return (
      <Panel kicker="Execution" title="Nothing to execute">
        <EmptyState title="No recommendation exists" />
      </Panel>
    );
  }

  const approved = approval?.status === "APPROVED";

  return (
    <div className="space-y-6">
      {/* ---- Execution ---- */}
      <Panel
        kicker="Execution"
        title="Simulated routing adapter"
        right={
          action ? (
            <StatusPill tone={action.status === "EXECUTED" ? "accent" : "critical"} dot>
              {action.status}
            </StatusPill>
          ) : (
            <StatusPill tone="muted">NOT EXECUTED</StatusPill>
          )
        }
      >
        {!approved && !action && (
          <StoppedState
            title="Approval required"
            why="No granted approval exists for this recommendation, so execution cannot be requested."
            next="A human must approve the recommendation first."
            tone="warning"
          />
        )}

        {approved && !action && (
          <>
            <p className="text-[13px] leading-relaxed text-muted-foreground">
              Execution runs server-side through <span className="font-mono">SimulatedRoutingAdapter</span>,
              which contacts nothing. The backend re-validates approval, expiry, staleness and
              policy before acting, and may still refuse.
            </p>
            <Button
              variant="primary"
              className="mt-4"
              onClick={() => void execute.run(rec.recommendation_id, approval?.approver_identity ?? "operator")}
              disabled={execute.pending}
            >
              {execute.pending ? "Executing…" : "Execute action"}
            </Button>
            {execute.error && <div className="mt-3"><ErrorState error={execute.error} compact /></div>}
          </>
        )}

        {action?.status === "REJECTED" && (
          <StoppedState
            title="Execution rejected"
            why={action.rejection_reason ?? "The backend refused this execution at revalidation."}
            next="Re-run analysis to produce a fresh recommendation against the current world."
            tone="critical"
          />
        )}

        {action && (
          <div className="mt-4 space-y-0.5 border-t border-border pt-4">
            <MonoField label="Action" value={String(action.action_id)} />
            <MonoField label="Adapter" value={action.adapter_name} />
            <MonoField label="Executed by" value={action.executed_by ?? "UNAVAILABLE"} />
            <MonoField label="Executed at" value={ts(action.executed_at)} />
            <MonoField label="Execution fingerprint" value={fp(action.execution_fingerprint, 20)} />
            <MonoField label="Simulated" value={action.is_simulated ? "TRUE (enforced by database)" : "FALSE"} />
          </div>
        )}
      </Panel>

      {/* ---- Verification ---- */}
      {action?.status === "EXECUTED" && (
        <>
          {!verification ? (
            <Panel kicker="Verification" title="Not yet verified" right={<StatusPill tone="warning" dot>PENDING</StatusPill>}>
              <p className="text-[13px] leading-relaxed text-muted-foreground">
                The action has executed, but it has <strong className="text-foreground">not</strong> been
                verified. Executed is not verified: verification independently measures the
                post-action population against the execution-time baseline, using its own
                thresholds, and can conclude the action did not help.
              </p>
              <Button
                variant="primary"
                className="mt-4"
                onClick={() => void verify.run(action.action_id)}
                disabled={verify.pending}
              >
                {verify.pending ? "Verifying…" : "Run independent verification"}
              </Button>
              {verify.error && <div className="mt-3"><ErrorState error={verify.error} compact /></div>}
            </Panel>
          ) : (
            <VerificationResult verification={verification} onOpenAudit={onOpenAudit} />
          )}
        </>
      )}
    </div>
  );
}

function VerificationResult({
  verification: v,
  onOpenAudit,
}: {
  verification: Verification;
  onOpenAudit: () => void;
}) {
  const effective = v.outcome === "RECOVERY_EFFECTIVE";
  const partial = v.outcome === "PARTIALLY_EFFECTIVE";
  const tone = effective ? "success" : partial ? "warning" : "critical";

  return (
    <>
      <Panel
        kicker="Independent Verification"
        title={(v.outcome ?? v.status).replace(/_/g, " ")}
        right={<StatusPill tone={tone} dot>{v.outcome ?? v.status}</StatusPill>}
      >
        {v.status === "INELIGIBLE" ? (
          <StoppedState
            title="Not verifiable"
            why={v.ineligible_reason ?? "This action cannot be verified."}
            next="Only an executed action has a post-action population to measure."
            tone="muted"
          />
        ) : (
          <>
            {/* Expected vs actual, never merged. */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <BeforeAfter
                label="Failure rate — baseline → actual simulated"
                before={`${pct(v.baseline.failure_rate)}%`}
                after={`${pct(v.actual_simulated.failure_rate)}%`}
                good={effective || partial}
                truthBefore="SIMULATED"
                truthAfter="VERIFIED"
              />
              <BeforeAfter
                label="GMV at risk — baseline → actual simulated"
                before={inr(v.baseline.gmv_at_risk)}
                after={inr(v.actual_simulated.gmv_at_risk)}
                good={effective || partial}
                truthBefore="SIMULATED"
                truthAfter="VERIFIED"
              />
            </div>

            <div className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-4">
              <Cell
                label="Projected Δ success"
                value={pctDelta(v.projected.success_delta)}
                truth="PROJECTED"
              />
              <Cell
                label="Measured Δ success"
                value={pctDelta(v.measured.success_delta)}
                truth="VERIFIED"
              />
              <Cell
                label="Variance vs projection"
                value={pctDelta(v.measured.variance_vs_projection)}
              />
              <Cell
                label="Attainment"
                value={
                  v.measured.attainment_ratio != null
                    ? `${(v.measured.attainment_ratio * 100).toFixed(0)}%`
                    : "UNAVAILABLE"
                }
              />
            </div>

            <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Big
                label="Actual simulated GMV recovered"
                value={inr(v.measured.gmv_recovered)}
                color="var(--verified)"
              />
              <Big label="Transactions moved" value={num(v.measured.transactions_moved)} />
              <Big label="Cohort population" value={num(v.measured.population)} />
            </div>

            {!effective && (
              <div className="mt-5">
                <StoppedState
                  title={v.outcome === "RECOVERY_NOT_VERIFIED" ? "Recovery not verified" : "Partially effective"}
                  why={v.reasons.join(" ")}
                  next={
                    v.outcome === "RECOVERY_NOT_VERIFIED"
                      ? "No recovery is claimed for this action. It contributes zero recovered GMV to the batch total."
                      : "The action helped, but attained less of the projection than required for a fully effective result."
                  }
                  tone={v.outcome === "RECOVERY_NOT_VERIFIED" ? "critical" : "warning"}
                />
              </div>
            )}

            {effective && (
              <ul className="mt-5 space-y-1.5">
                {v.reasons.map((r, i) => (
                  <li key={i} className="flex items-start gap-2 text-[13px] leading-relaxed text-muted-foreground">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden
                         className="mt-0.5 shrink-0 text-[color:var(--success)]">
                      <path d="m5 13 4 4L19 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    {r}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Panel>

      {/* Integrity is shown, not implied. */}
      <Panel
        kicker="Verification Integrity"
        title={v.integrity_passed ? "All checks passed" : "Integrity checks failed"}
        right={<StatusPill tone={v.integrity_passed ? "success" : "critical"} dot>
          {v.integrity_passed ? "PASSED" : "FAILED"}
        </StatusPill>}
      >
        <ul className="space-y-1.5">
          {v.integrity_checks.map((c) => (
            <li key={c.name} className="flex items-start justify-between gap-4 border-b border-border/60 py-1.5 last:border-0">
              <div className="min-w-0">
                <div className="font-mono text-[11px] text-foreground">{c.name}</div>
                <div className="text-[11px] leading-relaxed text-faint-foreground">{c.detail}</div>
              </div>
              <span
                className="shrink-0 font-mono text-[11px] font-semibold"
                style={{ color: c.passed ? "var(--success)" : "var(--critical)" }}
              >
                {c.passed ? "PASS" : "FAIL"}
              </span>
            </li>
          ))}
        </ul>
        <div className="mt-4 space-y-0.5 border-t border-border pt-4">
          <MonoField label="Verification" value={v.verification_id ? String(v.verification_id) : "UNAVAILABLE"} />
          <MonoField label="Fingerprint" value={fp(v.verification_fingerprint, 20)} />
        </div>
      </Panel>

      <Panel kicker="Provenance" title="What this measurement is, and is not">
        <Kicker>Recovery claim</Kicker>
        <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
          No production money was recovered. Both sides of every comparison are modelled
          outcomes over observed transaction amounts under a synthetic incident.
        </p>
        <div className="mt-4 flex justify-end">
          <Button variant="secondary" onClick={onOpenAudit}>
            View full audit trail →
          </Button>
        </div>
      </Panel>
    </>
  );
}

function Cell({ label, value, truth }: { label: string; value: string; truth?: "PROJECTED" | "VERIFIED" }) {
  return (
    <div className="bg-surface-1 p-3">
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">{label}</span>
      </div>
      <div className="tnum mt-1 font-display text-base font-semibold text-foreground">{value}</div>
      {truth && <div className="mt-1.5"><TruthTag truth={truth} /></div>}
    </div>
  );
}

function Big({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-0 p-4">
      <div className="text-[10px] font-medium uppercase tracking-[0.09em] text-muted-foreground">{label}</div>
      <div className="tnum mt-1.5 font-display text-[22px] font-semibold leading-none" style={{ color: color ?? "var(--foreground)" }}>
        {value}
      </div>
    </div>
  );
}
