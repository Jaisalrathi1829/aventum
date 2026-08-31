// ============================================================
// Presentation formatting ONLY.
//
// Everything here turns a number the backend already decided into a string. Nothing
// here decides anything: no thresholds, no rates derived from other rates, no money
// arithmetic (§8). If a value is absent it stays absent — `UNAVAILABLE` is a truthful
// answer and a fabricated 0 is not (§11).
// ============================================================

export const UNAVAILABLE = "UNAVAILABLE";

type MaybeNum = number | string | null | undefined;

function isAbsent(v: MaybeNum): v is null | undefined {
  return v === null || v === undefined;
}

/** A rate the backend supplied as 0..1, rendered as a percentage string. */
export function pct(value: MaybeNum, digits = 2): string {
  if (isAbsent(value)) return UNAVAILABLE;
  if (typeof value === "string") return value;
  return `${(value * 100).toFixed(digits)}`;
}

/** A signed percentage-point delta, e.g. "+3.41". */
export function pctDelta(value: MaybeNum, digits = 2): string {
  if (isAbsent(value)) return UNAVAILABLE;
  if (typeof value === "string") return value;
  const scaled = value * 100;
  return `${scaled >= 0 ? "+" : ""}${scaled.toFixed(digits)}`;
}

/** Indian-format rupee amount. The ₹ symbol is presentation; the number is the backend's. */
export function inr(value: MaybeNum, digits = 2): string {
  if (isAbsent(value)) return UNAVAILABLE;
  if (typeof value === "string") return value;
  return `₹${value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function num(value: MaybeNum, digits = 0): string {
  if (isAbsent(value)) return UNAVAILABLE;
  if (typeof value === "string") return value;
  return value.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function sigma(value: MaybeNum): string {
  if (isAbsent(value)) return UNAVAILABLE;
  if (typeof value === "string") return value;
  return `${value.toFixed(2)}σ`;
}

export function ms(value: MaybeNum): string {
  if (isAbsent(value)) return UNAVAILABLE;
  if (typeof value === "string") return value;
  return `${value.toFixed(0)} ms`;
}

/** Short fingerprint for display. The full value stays available in the title attribute. */
export function fp(value: string | null | undefined, chars = 12): string {
  if (!value) return UNAVAILABLE;
  return value.length <= chars ? value : `${value.slice(0, chars)}…`;
}

/** Timestamps are rendered in IST so every screen agrees regardless of host timezone. */
export function ts(value: string | null | undefined): string {
  if (!value) return UNAVAILABLE;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
  });
}

export function timeOnly(value: string | null | undefined): string {
  if (!value) return UNAVAILABLE;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "Asia/Kolkata",
  });
}

/** Turns SCREAMING_SNAKE backend vocabulary into a readable label, without renaming it. */
export function humanize(value: string | null | undefined): string {
  if (!value) return UNAVAILABLE;
  return value.replace(/_/g, " ");
}

/** How long until an approval or recommendation lapses, or null once it has. */
export function remaining(expiresAt: string | null | undefined, now: number = Date.now()): string | null {
  if (!expiresAt) return null;
  const end = new Date(expiresAt).getTime();
  if (Number.isNaN(end)) return null;
  const deltaMs = end - now;
  if (deltaMs <= 0) return null;
  const minutes = Math.floor(deltaMs / 60_000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}
