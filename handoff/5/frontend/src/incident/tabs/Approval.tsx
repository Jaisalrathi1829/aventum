import { useState } from "react";
import { Panel, Button, StatusPill, TruthTag, MonoField, Kicker } from "../../components/ui";
import { ErrorState, LoadingPanel, EmptyState, StoppedState } from "../../components/states";
import { api } from "../../lib/api";
import { useMutation, type Resource } from "../../lib/hooks";
import type { RecommendationBundle } from "../../lib/types";
import type { IncidentTab } from "../IncidentWorkspace";
import { fp, inr, num, pctDelta, remaining, ts } from "../../lib/format";

/**
 * Human approval — the one step no system in Aventum may take for itself.
 *
 * Every control here submits to the backend and then re-reads. Nothing in this file
 * sets an `approved` flag; APPROVED appears on screen only because a persisted row
 * came back saying so (§16).
 */
export function Approval({
  bundle,
  onChanged,
  onGo,
}: {
  bundle: Resource<RecommendationBundle>;
  onChanged: () => Promise<void>;
  onGo: (t: IncidentTab) => void;
}) {
  const [approver, setApprover] = useState("ops.lead@aventum.demo");
  const [note, setNote] = useState("");

  const requestApproval = useMutation(async (recommendationId: number) => {
    const r = await api.requestApproval(recommendationId);
    await onChanged();
    return r;
  });

  const decide = useMutation(async (approvalId: number, decision: "APPROVED" | "REJECTED") => {
    const r = await api.decideApproval(approvalId, decision, approver.trim(), note.trim() || undefined);
    await onChanged();
    return r;
  });

  if (bundle.initialLoading) return <LoadingPanel label="Loading approval" rows={5} />;
  if (bundle.error && !bundle.data) {
    return <ErrorState error={bundle.error} onRetry={() => void bundle.refresh()} />;
  }

  const rec = bundle.data?.recommendation ?? null;
  const approval = bundle.data?.approval ?? null;
  const stale = bundle.data?.stale ?? null;

  if (!rec) {
    return (
      <Panel kicker="Approval" title="Nothing to approve">
        <EmptyState title="No recommendation exists" detail="Produce one from the Simulation tab first." />
      </Panel>
    );
  }

  if (rec.action_type === "NO_ACTION") {
    return (
      <Panel kicker="Approval" title="No approval required">
        <StoppedState
          title="No action"
          why="NO_ACTION changes nothing, so there is nothing for a human to authorise."
          next="This incident terminates here unless new evidence changes the diagnosis."
          tone="muted"
        />
      </Panel>
    );
  }

  if (rec.policy.validation_result !== "PERMITTED") {
    return (
      <Panel kicker="Approval" title="Blocked before approval">
        <StoppedState
          title="Policy blocked"
          why={`The policy gate returned ${rec.policy.validation_result}. A blocked recommendation is never presented to a human, because showing it would invite an approval the policy already refused.`}
          next="No approval can be requested."
          tone="critical"
        />
      </Panel>
    );
  }

  const pending = approval?.status === "PENDING";
  const timeLeft = pending ? remaining(approval.expires_at) : null;
  const expiredByClock = pending && timeLeft === null;

  return (
    <div className="space-y-6">
      {stale?.is_stale && (
        <StoppedState title="Stale" why={stale.reasons.join(" ")} next={stale.next_step} tone="warning" />
      )}

      {/* What the human is being asked to authorise. */}
      <Panel
        kicker="Approval Request"
        title={`${rec.action_type} · ${rec.source_gateway_id} → ${rec.target_gateway_id} @ ${num(rec.traffic_percentage)}%`}
        right={
          approval ? (
            <StatusPill
              tone={
                approval.status === "APPROVED" ? "success"
                : approval.status === "REJECTED" || approval.status === "EXPIRED" ? "critical"
                : "warning"
              }
              dot
            >
              {approval.status}
            </StatusPill>
          ) : (
            <StatusPill tone="muted">NOT REQUESTED</StatusPill>
          )
        }
      >
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Fig label="Projected GMV retained" value={inr(rec.expected.expected_gmv_retained)} tone="projected" />
          <Fig label="Projected Δ success" value={pctDelta(rec.expected.expected_success_delta)} />
          <Fig label="Risk score" value={rec.risk.risk_score?.toFixed(4) ?? "UNAVAILABLE"} />
          <Fig label="Capacity" value="UNAVAILABLE" />
        </div>

        <div className="mt-5 rounded-md border border-[color:var(--warning)]/25 bg-[color:var(--warning)]/[0.05] px-3 py-2.5">
          <p className="text-[12px] leading-relaxed text-muted-foreground">
            This incident is synthetic and execution is simulated. No real payment
            infrastructure is contacted and no real gateway is rerouted.
          </p>
        </div>
      </Panel>

      {/* Step 1: raise the request. */}
      {!approval && (
        <Panel kicker="Step 1" title="Request human approval">
          <p className="text-[13px] leading-relaxed text-muted-foreground">
            Raising an approval hands the decision to a person. The agent cannot do this on
            its own behalf, and it cannot answer it.
          </p>
          <Button
            variant="primary"
            className="mt-4"
            onClick={() => void requestApproval.run(rec.recommendation_id)}
            disabled={requestApproval.pending || stale?.is_stale}
          >
            {requestApproval.pending ? "Requesting…" : "Request approval"}
          </Button>
          {requestApproval.error && <div className="mt-3"><ErrorState error={requestApproval.error} compact /></div>}
        </Panel>
      )}

      {/* Step 2: the human decides. */}
      {pending && (
        <Panel
          kicker="Step 2"
          title="Human decision"
          right={<TruthTag truth="HUMAN" />}
        >
          {expiredByClock ? (
            <StoppedState
              title="Approval window closed"
              why="This approval's validity window has passed. The backend will refuse a decision on it."
              next="Request a fresh approval, which will re-check the recommendation against the current world."
              tone="critical"
            />
          ) : (
            <p className="text-[13px] leading-relaxed text-muted-foreground">
              Expires in <span className="tnum font-mono text-foreground">{timeLeft}</span> ·{" "}
              {ts(approval.expires_at)}
            </p>
          )}

          <div className="mt-4 space-y-3">
            <label className="block">
              <span className="text-[11px] font-medium uppercase tracking-[0.09em] text-muted-foreground">
                Approver identity
              </span>
              <input
                type="text"
                value={approver}
                onChange={(e) => setApprover(e.target.value)}
                required
                className="mt-1.5 w-full rounded-md border border-border-strong bg-surface-0 px-3 py-2 font-mono text-[13px] text-foreground outline-none focus-visible:border-accent"
              />
            </label>
            <label className="block">
              <span className="text-[11px] font-medium uppercase tracking-[0.09em] text-muted-foreground">
                Decision note (optional)
              </span>
              <input
                type="text"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="mt-1.5 w-full rounded-md border border-border-strong bg-surface-0 px-3 py-2 text-[13px] text-foreground outline-none focus-visible:border-accent"
              />
            </label>
          </div>

          <div className="mt-4 flex gap-3">
            <Button
              variant="primary"
              onClick={() => void decide.run(approval.approval_id, "APPROVED")}
              disabled={decide.pending || !approver.trim()}
            >
              {decide.pending ? "Submitting…" : "Approve"}
            </Button>
            <Button
              variant="danger"
              onClick={() => void decide.run(approval.approval_id, "REJECTED")}
              disabled={decide.pending || !approver.trim()}
            >
              Reject
            </Button>
          </div>
          {decide.error && <div className="mt-3"><ErrorState error={decide.error} compact /></div>}
          <p className="mt-3 text-[11px] leading-relaxed text-faint-foreground">
            An approval with no attributable person is not an approval. The backend refuses a
            decision without an identity.
          </p>
        </Panel>
      )}

      {/* The recorded decision. */}
      {approval && approval.status !== "PENDING" && (
        <Panel kicker="Recorded Decision" title={approval.status} right={<TruthTag truth="HUMAN" />}>
          {approval.status === "REJECTED" && (
            <StoppedState
              title="Approval declined"
              why={approval.decision_note || "A human declined this recommendation."}
              next="No action will be executed. The recommendation stops here."
              tone="critical"
            />
          )}
          {approval.status === "EXPIRED" && (
            <StoppedState
              title="Approval expired"
              why="The approval window closed before a decision was recorded."
              next="Re-run analysis to produce a fresh recommendation against the current world."
              tone="critical"
            />
          )}

          <div className="mt-4 space-y-0.5">
            <MonoField label="Approval" value={String(approval.approval_id)} />
            <MonoField label="Approver" value={approval.approver_identity ?? "UNAVAILABLE"} />
            <MonoField label="Decided" value={ts(approval.decided_at)} />
            <MonoField label="Expires" value={ts(approval.expires_at)} />
            <MonoField label="Note" value={approval.decision_note ?? "—"} />
            <MonoField label="Recommendation fingerprint" value={fp(approval.approval_fingerprint, 20)} />
          </div>

          {approval.status === "APPROVED" && (
            <div className="mt-5 flex justify-end">
              <Button variant="primary" onClick={() => onGo("verification")}>
                Continue to execution →
              </Button>
            </div>
          )}
        </Panel>
      )}

      {/* The immutable artifact the human actually saw. */}
      {approval?.payload && (
        <Panel kicker="Approval Artifact" title="What was presented for decision">
          <Kicker>Stored with the approval, so it survives export</Kicker>
          <pre className="mt-3 max-h-72 overflow-auto rounded-md border border-border bg-surface-0 p-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
            {JSON.stringify(approval.payload, null, 2)}
          </pre>
        </Panel>
      )}
    </div>
  );
}

function Fig({ label, value, tone }: { label: string; value: string; tone?: "projected" }) {
  return (
    <div>
      <div className="text-[10px] font-medium uppercase tracking-[0.09em] text-muted-foreground">{label}</div>
      <div
        className="tnum mt-1 font-display text-lg font-semibold"
        style={{ color: tone === "projected" ? "var(--projected)" : "var(--foreground)" }}
      >
        {value}
      </div>
    </div>
  );
}
