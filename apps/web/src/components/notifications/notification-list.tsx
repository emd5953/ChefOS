"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";
import { keys } from "@/lib/queries";
import type { AppNotification } from "@/lib/types";
import { useNow } from "@/lib/use-now";

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { ids?: string[]; all?: boolean }) =>
      api<{ unread_count: number }>("/v1/notifications/read", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.notifications }),
  });
}

/** Opening a notification marks it read and follows its deep link. */
export function NotificationItems({
  items,
  onOpen,
  compact,
}: {
  items: AppNotification[];
  onOpen?: () => void;
  compact?: boolean;
}) {
  const router = useRouter();
  const now = useNow();
  const markRead = useMarkRead();
  return (
    <ul className="divide-y divide-border">
      {items.map((n) => (
        <li key={n.id}>
          <button
            type="button"
            onClick={() => {
              if (!n.read_at) markRead.mutate({ ids: [n.id] });
              onOpen?.();
              if (n.link) router.push(n.link);
            }}
            className={cn(
              "flex w-full gap-3 text-left transition-colors hover:bg-muted",
              compact ? "px-3 py-2.5" : "px-4 py-3.5",
            )}
          >
            <span
              aria-hidden
              className={cn("mt-1.5 size-2 shrink-0 rounded-full", n.read_at ? "bg-transparent" : "bg-accent")}
            />
            <span className="min-w-0 flex-1">
              <span className={cn("block text-[13px] leading-snug", n.read_at ? "text-foreground/70" : "font-semibold")}>
                {n.title}
                {!n.read_at && <span className="sr-only"> (unread)</span>}
              </span>
              {n.body && <span className="mt-0.5 block text-xs leading-relaxed text-foreground/55">{n.body}</span>}
              <span className="mt-1 block text-[11px] text-foreground/40">{formatRelative(n.created_at, now)}</span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
