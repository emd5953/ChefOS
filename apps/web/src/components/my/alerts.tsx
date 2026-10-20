"use client";

import { Bell } from "lucide-react";
import { NotificationItems, useMarkRead } from "@/components/notifications/notification-list";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useNotifications } from "@/lib/queries";

export function MyAlerts() {
  const q = useNotifications();
  const markRead = useMarkRead();
  const unread = q.data?.unread_count ?? 0;
  return (
    <>
      <div className="flex items-end justify-between gap-3">
        <h1 className="text-[30px] font-semibold leading-tight tracking-[-0.05em]">Alerts</h1>
        {unread > 0 && (
          <button type="button" onClick={() => markRead.mutate({ all: true })} className="pb-1.5 text-xs font-semibold text-accent">
            Mark all read
          </button>
        )}
      </div>
      <Card className="mt-5 overflow-hidden">
        {q.isPending ? (
          <div className="space-y-3 p-4" role="status" aria-label="Loading alerts">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : q.data.data.length === 0 ? (
          <EmptyState icon={<Bell />} title="No alerts" description="Schedule changes and request decisions show up here." />
        ) : (
          <NotificationItems items={q.data.data} />
        )}
      </Card>
    </>
  );
}
