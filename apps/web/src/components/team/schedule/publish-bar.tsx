"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage, isApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { keys } from "@/lib/queries";
import type { PublishResult, ScheduleResponse } from "@/lib/types";
import { conflictLabel } from "./shift-dialog";

/** Sticky bar: unpublished change count and Publish. Conflicts other than overlaps need an
 * explicit acknowledgement; a stale view (someone else edited) refetches instead. */
export function PublishBar({ data }: { data: ScheduleResponse }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const meta = data.schedule;
  const n = meta.unpublished_changes;
  const blocking = data.conflicts.filter((c) => c.kind === "overlap");
  const counts = new Map<string, number>();
  for (const c of data.conflicts) if (c.kind !== "overlap") counts.set(c.kind, (counts.get(c.kind) ?? 0) + 1);

  const publish = useMutation({
    mutationFn: (acknowledge: boolean) =>
      api<PublishResult>("/v1/team/schedule/publish", {
        method: "POST",
        query: { location_id: data.location_id, week_start: meta.week_start },
        body: { expected_version: meta.version, acknowledge_conflicts: acknowledge },
      }),
    onSuccess: async (r) => {
      setConfirming(false);
      await qc.invalidateQueries({ queryKey: keys.team });
      if (!r.changed) return toast("Nothing changed since the last publish.", "info");
      const missing = r.employees_without_login.length;
      toast(
        `Published. ${r.notified_users} ${r.notified_users === 1 ? "person" : "people"} notified` +
          (missing ? `; ${missing} without a login won’t see it in the app.` : "."),
        "success",
      );
    },
    onError: async (e) => {
      if (isApiError(e) && e.code === "conflicts_unacknowledged") return setConfirming(true);
      setConfirming(false);
      if (isApiError(e) && e.code === "schedule_changed") await qc.invalidateQueries({ queryKey: keys.team });
      toast(errorMessage(e), "error");
    },
  });

  if (n === 0 && meta.published_version != null) {
    return (
      <p className="text-center text-xs text-foreground/50">
        Published{meta.published_at ? ` ${formatDateTime(meta.published_at, data.timezone)}` : ""}. Employees see this week.
      </p>
    );
  }
  if (n === 0) return null;

  return (
    <>
      <div className="sticky bottom-4 z-20 mx-auto flex w-fit max-w-full flex-wrap items-center justify-center gap-x-4 gap-y-2 rounded-full bg-foreground py-2 pl-5 pr-2 text-[13px] text-background shadow-lg">
        <span className="font-semibold tabular">
          {n} unpublished change{n === 1 ? "" : "s"}
        </span>
        {blocking.length > 0 && <span className="text-red-300">Fix {blocking.length} overlap{blocking.length === 1 ? "" : "s"} first</span>}
        <Button
          size="sm"
          variant="accent"
          onClick={() => publish.mutate(false)}
          loading={publish.isPending}
          disabled={blocking.length > 0}
        >
          <Send aria-hidden /> Publish
        </Button>
      </div>
      <Dialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Publish with conflicts?"
        description="These don’t block publishing, but employees will see the schedule as it is."
      >
        <ul className="mb-5 space-y-1 text-[13px]">
          {[...counts.entries()].map(([kind, count]) => (
            <li key={kind} className="flex justify-between rounded-lg bg-muted px-3 py-2">
              <span>{conflictLabel(kind)}</span>
              <span className="font-semibold tabular">{count}</span>
            </li>
          ))}
        </ul>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setConfirming(false)}>
            Review first
          </Button>
          <Button onClick={() => publish.mutate(true)} loading={publish.isPending}>
            Publish anyway
          </Button>
        </div>
      </Dialog>
    </>
  );
}
