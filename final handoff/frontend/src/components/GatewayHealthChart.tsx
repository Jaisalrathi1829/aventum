import { TruthTag } from "./ui";
import type { GatewayHealth } from "../lib/types";

/**
 * Baseline versus under-incident failure probability, per gateway.
 *
 * This replaces the prototype's seven-day success-rate timeline, which was a fabricated
 * series with no backend behind it. §5 is explicit that fake behaviour must not survive
 * because it demos well, and there is no time-series endpoint to make it real — so the
 * chart now shows a comparison the system can actually defend.
 *
 * Both numbers come from the counterfactual engine's own `runtime_profile_for`, so the
 * bars show exactly what the simulator reasons with.
 */
export function GatewayHealthChart({
  gateways,
  height = 200,
}: {
  gateways: GatewayHealth[];
  height?: number;
}) {
  if (gateways.length === 0) {
    return (
      <p className="py-8 text-center text-[13px] text-faint-foreground">
        Gateway health is UNAVAILABLE for this incident.
      </p>
    );
  }

  const w = 720;
  const padL = 44;
  const padR = 16;
  const padT = 14;
  const padB = 30;
  const plotW = w - padL - padR;
  const plotH = height - padT - padB;

  // Scale to the largest effective probability so the degraded gateway is legible
  // rather than compressed against a fixed 100% ceiling.
  const maxValue = Math.max(
    0.05,
    ...gateways.map((g) => Math.max(g.effective_failure_probability ?? 0, g.baseline_failure_probability ?? 0)),
  );
  const scale = (v: number) => (v / (maxValue * 1.15)) * plotH;
  const bandW = plotW / gateways.length;

  const ticks = [0, maxValue / 2, maxValue].map((v) => ({
    v,
    y: padT + plotH - scale(v),
  }));

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <LegendSwatch color="var(--muted-foreground)" label="Baseline" />
        <LegendSwatch color="var(--critical)" label="Under incident" />
        <TruthTag truth="SYNTHETIC" className="ml-auto" />
      </div>

      <svg
        viewBox={`0 0 ${w} ${height}`}
        className="w-full"
        style={{ height }}
        role="img"
        aria-label="Baseline versus under-incident failure probability by gateway"
      >
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={padL} x2={w - padR} y1={t.y} y2={t.y} stroke="var(--border)" strokeWidth="1" />
            <text x={padL - 8} y={t.y + 3} textAnchor="end" className="tnum"
                  fill="var(--faint-foreground)" fontSize="10" fontFamily="var(--font-mono)">
              {(t.v * 100).toFixed(0)}%
            </text>
          </g>
        ))}

        {gateways.map((g, i) => {
          const cx = padL + bandW * i + bandW / 2;
          const barW = Math.min(26, bandW * 0.3);
          const baseH = scale(g.baseline_failure_probability ?? 0);
          const effH = scale(g.effective_failure_probability ?? 0);
          const baseY = padT + plotH - baseH;
          const effY = padT + plotH - effH;
          return (
            <g key={g.gateway_id}>
              <rect x={cx - barW - 3} y={baseY} width={barW} height={Math.max(1, baseH)}
                    fill="var(--muted-foreground)" opacity={0.45} rx="2" />
              <rect x={cx + 3} y={effY} width={barW} height={Math.max(1, effH)}
                    fill={g.is_affected ? "var(--critical)" : "var(--synthetic)"}
                    opacity={g.is_affected ? 0.95 : 0.5} rx="2" />
              <text x={cx} y={height - 14} textAnchor="middle"
                    fill={g.is_affected ? "var(--critical)" : "var(--muted-foreground)"}
                    fontSize="10" fontFamily="var(--font-mono)"
                    fontWeight={g.is_affected ? 600 : 400}>
                {g.gateway_id.replace("gateway_", "")}
              </text>
              {g.is_affected && (
                <text x={cx} y={effY - 6} textAnchor="middle" className="tnum"
                      fill="var(--critical)" fontSize="10" fontFamily="var(--font-mono)" fontWeight="600">
                  {((g.effective_failure_probability ?? 0) * 100).toFixed(1)}%
                </text>
              )}
            </g>
          );
        })}
        <line x1={padL} x2={w - padR} y1={padT + plotH} y2={padT + plotH} stroke="var(--border-strong)" strokeWidth="1" />
      </svg>

      {/* The same data as a table, so the chart is not the only way to read it (§35). */}
      <table className="sr-only">
        <caption>Failure probability by gateway</caption>
        <thead>
          <tr><th>Gateway</th><th>Baseline</th><th>Under incident</th></tr>
        </thead>
        <tbody>
          {gateways.map((g) => (
            <tr key={g.gateway_id}>
              <td>{g.gateway_id}</td>
              <td>{((g.baseline_failure_probability ?? 0) * 100).toFixed(2)}%</td>
              <td>{((g.effective_failure_probability ?? 0) * 100).toFixed(2)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
      <span className="size-2 rounded-[2px]" style={{ backgroundColor: color }} aria-hidden />
      {label}
    </span>
  );
}
