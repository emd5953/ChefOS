"use client";

import type { ScheduleResponse } from "@/lib/types";

/** Sticky status of the draft. */
export function PublishBar({ data }: { data: ScheduleResponse }) {
  const n = data.schedule.unpublished_changes;
  if (n === 0) return null;
  return (
    <div className="sticky bottom-4 z-20 mx-auto flex w-fit items-center gap-3 rounded-full bg-foreground px-5 py-2.5 text-[13px] text-background shadow-lg">
      <span className="font-semibold tabular">
        {n} unpublished change{n === 1 ? "" : "s"}
      </span>
      <span className="text-background/60">Drafts are visible only to managers</span>
    </div>
  );
}
