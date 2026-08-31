import { useCallback, useEffect, useState } from "react";
import { Sidebar, TopBar, SimBanner } from "./components/Shell";
import type { Nav } from "./components/Shell";
import { Overview } from "./screens/Overview";
import { Audit } from "./screens/Audit";
import { IncidentWorkspace } from "./incident/IncidentWorkspace";
import { ErrorBoundary, ErrorState, LoadingPanel } from "./components/states";
import { api } from "./lib/api";
import { useResource } from "./lib/hooks";

export default function App() {
  const [nav, setNav] = useState<Nav>("overview");
  const [incidentId, setIncidentId] = useState<number | null>(null);
  const [clock, setClock] = useState(() => fmt(new Date()));

  useEffect(() => {
    const id = setInterval(() => setClock(fmt(new Date())), 1000);
    return () => clearInterval(id);
  }, []);

  // Health drives the sidebar indicator and, critically, whether the agent is reported
  // as available. It is polled slowly: it is a status light, not a data feed.
  const health = useResource(() => api.health(), []);
  useEffect(() => {
    const id = setInterval(() => void health.refresh(), 20_000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const overview = useResource(() => api.overview(), []);

  const openIncident = useCallback((id: number) => {
    setIncidentId(id);
    setNav("incident");
  }, []);

  // The incident screen needs an incident. If the operator lands there without one,
  // fall back to the highest-significance incident the backend reported rather than
  // guessing an ID.
  const effectiveIncidentId = incidentId ?? overview.data?.primary_incident?.incident_id ?? null;

  return (
    <div className="flex h-full w-full overflow-hidden bg-background text-foreground">
      <Sidebar
        nav={nav}
        onNav={setNav}
        incidentCount={overview.data?.active_incident_count ?? 0}
        health={health.data}
        healthFailed={health.error !== null && health.data === null}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar clock={clock} environment={overview.data?.environment ?? health.data?.environment ?? null} />
        <SimBanner environment={overview.data?.environment ?? health.data?.environment ?? null} />
        <main className="min-h-0 flex-1 overflow-hidden">
          <ErrorBoundary label="This screen could not be rendered">
            {nav === "overview" && (
              <div className="h-full overflow-y-auto">
                <Overview resource={overview} onOpenIncident={openIncident} />
              </div>
            )}
            {nav === "incident" &&
              (effectiveIncidentId === null ? (
                <div className="mx-auto max-w-[1280px] px-8 py-6">
                  {overview.initialLoading ? (
                    <LoadingPanel label="Loading incidents" rows={4} />
                  ) : overview.error ? (
                    <ErrorState error={overview.error} onRetry={() => void overview.refresh()} />
                  ) : (
                    <p className="text-[13px] text-muted-foreground">
                      No incident is available to open.
                    </p>
                  )}
                </div>
              ) : (
                <IncidentWorkspace
                  incidentId={effectiveIncidentId}
                  agentReachable={health.data?.agent.ok ?? false}
                  onOpenAudit={() => setNav("audit")}
                  onWorkflowChanged={() => void overview.refresh()}
                />
              ))}
            {nav === "audit" && (
              <div className="h-full overflow-y-auto">
                <Audit incidentId={effectiveIncidentId} />
              </div>
            )}
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}

function fmt(d: Date) {
  // Render as IST clock regardless of host timezone.
  return d.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "Asia/Kolkata",
  });
}
