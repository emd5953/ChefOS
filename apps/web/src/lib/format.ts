const usd0 = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
  minimumFractionDigits: 0,
});
const usd2 = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const int = new Intl.NumberFormat("en-US");

export const EM_DASH = "—";

/**
 * Format integer cents as USD. `auto` (default) drops decimals for amounts of
 * $1,000 or more (KPI tiles); pass `2` to always show cents (avg check).
 */
export function formatMoney(
  cents: number | null | undefined,
  decimals: "auto" | 0 | 2 = "auto",
): string {
  if (cents == null || Number.isNaN(cents)) return EM_DASH;
  const dollars = cents / 100;
  const useZero = decimals === 0 || (decimals === "auto" && Math.abs(dollars) >= 1000);
  return (useZero ? usd0 : usd2).format(dollars);
}

/** Compact axis label, e.g. 125000 cents -> "$1.3k". */
export function formatMoneyCompact(cents: number): string {
  const d = cents / 100;
  if (Math.abs(d) >= 1_000_000) return `$${(d / 1_000_000).toFixed(1)}m`;
  if (Math.abs(d) >= 1000) return `$${(d / 1000).toFixed(d % 1000 === 0 ? 0 : 1)}k`;
  return `$${Math.round(d)}`;
}

export function formatInt(n: number | null | undefined): string {
  return n == null ? EM_DASH : int.format(n);
}

/** Percent value already in percent units (28.4 -> "28.4%"). */
export function formatPct(pct: number | null | undefined, digits = 1): string {
  if (pct == null || Number.isNaN(pct)) return EM_DASH;
  return `${pct.toFixed(digits)}%`;
}

export function formatHours(h: number): string {
  return `${h.toFixed(1)} h`;
}

const MIN = 60_000;
export function formatRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const diff = now - new Date(iso).getTime();
  if (Number.isNaN(diff)) return EM_DASH;
  if (diff < MIN) return "just now";
  if (diff < 60 * MIN) return `${Math.floor(diff / MIN)}m ago`;
  if (diff < 24 * 60 * MIN) return `${Math.floor(diff / (60 * MIN))}h ago`;
  return `${Math.floor(diff / (24 * 60 * MIN))}d ago`;
}

export function formatClock(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  });
}

export function formatDateTime(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  });
}

/** "2026-10-19" -> "Oct 19" (parsed as a calendar date, no timezone shift). */
export function formatDate(date: string, withYear = false): string {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: withYear ? "numeric" : undefined,
    timeZone: "UTC",
  });
}

export function formatWeekday(date: string): string {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1)).toLocaleDateString("en-US", {
    weekday: "short",
    timeZone: "UTC",
  });
}

export function formatHour(h: number): string {
  const suffix = h < 12 ? "a" : "p";
  const hr = h % 12 === 0 ? 12 : h % 12;
  return `${hr}${suffix}`;
}

/** Compact shift time in a zone: "4p", "4:30p". */
export function formatShiftTime(iso: string, timeZone: string): string {
  const d = new Date(iso);
  const parts = new Intl.DateTimeFormat("en-US", { timeZone, hour: "numeric", minute: "2-digit", hourCycle: "h12" })
    .formatToParts(d);
  const hour = parts.find((p) => p.type === "hour")?.value ?? "";
  const minute = parts.find((p) => p.type === "minute")?.value ?? "00";
  const period = (parts.find((p) => p.type === "dayPeriod")?.value ?? "").toLowerCase().startsWith("p") ? "p" : "a";
  return `${hour}${minute === "00" ? "" : `:${minute}`}${period}`;
}

export function formatShiftRange(startIso: string, endIso: string, timeZone: string): string {
  return `${formatShiftTime(startIso, timeZone)}–${formatShiftTime(endIso, timeZone)}`;
}
