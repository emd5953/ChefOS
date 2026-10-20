import { Loader2 } from "lucide-react";
import { Badge, Dot, type Tone } from "@/components/ui/badge";
import { diffDays } from "@/lib/dates";
import type { ConnectionStatus, StreamHealth, SyncRun } from "@/lib/types";

const CONNECTION: Record<ConnectionStatus, { tone: Tone; label: string }> = {
  active: { tone: "success", label: "Active" },
  pending: { tone: "warning", label: "Pending" },
  needs_reauth: { tone: "danger", label: "Needs reauthorization" },
  error: { tone: "danger", label: "Error" },
  disconnected: { tone: "neutral", label: "Disconnected" },
};

export function ConnectionPill({ status }: { status: ConnectionStatus }) {
  const c = CONNECTION[status];
  return (
    <Badge tone={c.tone}>
      <Dot tone={c.tone} pulse={status === "active"} />
      {c.label}
    </Badge>
  );
}

const STREAM: Record<StreamHealth["status"], { tone: Tone; label: string }> = {
  fresh: { tone: "success", label: "Fresh" },
  stale: { tone: "warning", label: "Stale" },
  failing: { tone: "danger", label: "Failing" },
  pending: { tone: "neutral", label: "Pending" },
};

export function StreamPill({ status }: { status: StreamHealth["status"] }) {
  const c = STREAM[status];
  return (
    <Badge tone={c.tone}>
      <Dot tone={c.tone} />
      {c.label}
    </Badge>
  );
}

export function RunPill({ status }: { status: SyncRun["status"] }) {
  if (status === "running") {
    return (
      <Badge tone="accent">
        <Loader2 className="size-3 animate-spin" aria-hidden /> Running
      </Badge>
    );
  }
  return status === "succeeded" ? (
    <Badge tone="success">Succeeded</Badge>
  ) : (
    <Badge tone="danger">Failed</Badge>
  );
}

export const STREAM_LABEL: Record<string, string> = {
  locations: "Locations",
  catalog: "Catalog",
  orders: "Orders",
  team_members: "Team members",
  timecards: "Timecards",
};

export type Backfill = { kind: "none" } | { kind: "running" | "done"; pct: number; oldestDone: string | null };

/**
 * Backfill runs newest -> oldest, so `backfill_completed_through` is the oldest
 * date finished. Progress = days covered (today back to that date) / days in
 * the whole window (today back to `backfill_target_from`).
 */
export function backfillOf(s: StreamHealth, today: string): Backfill {
  const target = s.backfill_target_from;
  if (!target) return { kind: "none" };
  const total = Math.max(1, diffDays(today, target) + 1);
  const through = s.backfill_completed_through;
  if (!through) return { kind: "running", pct: 0, oldestDone: null };
  if (through <= target) return { kind: "done", pct: 100, oldestDone: through };
  const covered = Math.min(total, Math.max(0, diffDays(today, through) + 1));
  return { kind: "running", pct: Math.min(99, Math.round((covered / total) * 100)), oldestDone: through };
}
