"use client";

import Link from "next/link";
import { Dot, type Tone } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { formatClock, formatRelative } from "@/lib/format";
import { useLocationScope } from "@/lib/location-scope";
import type { Freshness, FreshnessStatus } from "@/lib/types";
import { useNow } from "@/lib/use-now";

// failing > stale > not_connected > fresh
const RANK: Record<FreshnessStatus, number> = { fresh: 0, not_connected: 1, stale: 2, failing: 3 };

export function worstFreshness(freshness: Freshness[]): { status: FreshnessStatus; asOf: string | null } {
  if (freshness.length === 0) return { status: "not_connected", asOf: null };
  let status: FreshnessStatus = "fresh";
  for (const f of freshness) if (RANK[f.status] > RANK[status]) status = f.status;
  // Oldest timestamp among entries at the worst status is the honest "as of".
  const stamps = freshness
    .filter((f) => f.status === status && f.data_as_of)
    .map((f) => f.data_as_of as string)
    .sort();
  return { status, asOf: stamps[0] ?? null };
}

const TONE: Record<FreshnessStatus, Tone> = {
  fresh: "success",
  stale: "warning",
  failing: "danger",
  not_connected: "neutral",
};

export function FreshnessBadge({
  freshness,
  timeZone,
  className,
}: {
  freshness: Freshness[];
  timeZone?: string;
  className?: string;
}) {
  const now = useNow();
  const scope = useLocationScope();
  const { status, asOf } = worstFreshness(freshness);

  const text =
    status === "fresh"
      ? asOf
        ? `Live · updated ${formatRelative(asOf, now)}`
        : "Live"
      : status === "stale"
        ? asOf
          ? `Stale · as of ${formatClock(asOf, timeZone)}`
          : "Stale"
        : status === "failing"
          ? "Sync failing"
          : "Not connected";

  const tone = TONE[status];
  const classes = cn(
    "inline-flex h-8 items-center gap-2 rounded-full border px-3 text-xs font-semibold tracking-[-0.005em] whitespace-nowrap transition-[background-color,border-color,transform] duration-150",
    tone === "success" && "border-success/25 bg-success/8 text-success",
    tone === "warning" && "border-warning/30 bg-warning/10 text-warning",
    tone === "danger" && "border-destructive/30 bg-destructive/8 text-destructive",
    tone === "neutral" && "border-border bg-muted text-foreground/55",
    className,
  );
  const inner = (
    <>
      <Dot tone={tone} pulse={status === "fresh"} />
      {text}
    </>
  );

  if (status === "failing") {
    return (
      <Link
        href={scope.href("/app/integrations")}
        className={cn(classes, "hover:bg-destructive/12 active:scale-[0.97]")}
        title="Open Integrations to see what failed"
      >
        {inner}
      </Link>
    );
  }
  return (
    <span role="status" className={classes}>
      {inner}
    </span>
  );
}
