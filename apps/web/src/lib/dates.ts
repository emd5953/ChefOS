import type { CompareMode, DateRange } from "./types";

const DAY = 86_400_000;

function parse(date: string): number {
  const [y, m, d] = date.split("-").map(Number);
  return Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}
function stamp(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

export function addDays(date: string, n: number): string {
  return stamp(parse(date) + n * DAY);
}
export function diffDays(a: string, b: string): number {
  return Math.round((parse(a) - parse(b)) / DAY);
}

/** Today's calendar date (YYYY-MM-DD) in an IANA timezone. */
export function todayIn(timeZone?: string, now = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(now);
    return parts;
  } catch {
    return stamp(now.getTime());
  }
}

export function eachDay(from: string, to: string): string[] {
  const out: string[] = [];
  for (let d = from; d <= to && out.length < 800; d = addDays(d, 1)) out.push(d);
  return out;
}

export type PresetId =
  | "last_7"
  | "last_30"
  | "last_90"
  | "this_month"
  | "last_month"
  | "ytd"
  | "custom";

export const PRESETS: Array<{ id: PresetId; label: string }> = [
  { id: "last_7", label: "Last 7 days" },
  { id: "last_30", label: "Last 30 days" },
  { id: "last_90", label: "Last 90 days" },
  { id: "this_month", label: "This month" },
  { id: "last_month", label: "Last month" },
  { id: "ytd", label: "Year to date" },
  { id: "custom", label: "Custom" },
];

export function isPreset(v: string | null | undefined): v is PresetId {
  return PRESETS.some((p) => p.id === v);
}

export function presetRange(id: Exclude<PresetId, "custom">, today: string): DateRange {
  const monthStart = `${today.slice(0, 7)}-01`;
  switch (id) {
    case "last_7":
      return { from: addDays(today, -6), to: today };
    case "last_30":
      return { from: addDays(today, -29), to: today };
    case "last_90":
      return { from: addDays(today, -89), to: today };
    case "this_month":
      return { from: monthStart, to: today };
    case "last_month": {
      const end = addDays(monthStart, -1);
      return { from: `${end.slice(0, 7)}-01`, to: end };
    }
    case "ytd":
      return { from: `${today.slice(0, 4)}-01-01`, to: today };
  }
}

/** The comparison window the API will use (docs/api.md). */
export function compareRange(range: DateRange, mode: CompareMode): DateRange {
  if (mode === "previous_year") {
    return { from: addDays(range.from, -364), to: addDays(range.to, -364) };
  }
  const len = diffDays(range.to, range.from) + 1;
  return { from: addDays(range.from, -len), to: addDays(range.from, -1) };
}

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return "America/New_York";
  }
}

export const US_TIMEZONES: Array<{ value: string; label: string }> = [
  { value: "America/New_York", label: "Eastern (New York)" },
  { value: "America/Chicago", label: "Central (Chicago)" },
  { value: "America/Denver", label: "Mountain (Denver)" },
  { value: "America/Phoenix", label: "Mountain, no DST (Phoenix)" },
  { value: "America/Los_Angeles", label: "Pacific (Los Angeles)" },
  { value: "America/Anchorage", label: "Alaska (Anchorage)" },
  { value: "Pacific/Honolulu", label: "Hawaii (Honolulu)" },
];
