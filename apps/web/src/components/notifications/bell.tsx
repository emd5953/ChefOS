"use client";

import { Popover } from "@base-ui-components/react/popover";
import { Bell as BellIcon } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/cn";
import { useNotifications } from "@/lib/queries";
import { NotificationItems, useMarkRead } from "./notification-list";

/** Header bell for the manager shell: unread count and the latest alerts. */
export function Bell({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const q = useNotifications();
  const markRead = useMarkRead();
  const unread = q.data?.unread_count ?? 0;
  const items = (q.data?.data ?? []).slice(0, 8);
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        className={cn(
          "relative grid size-10 place-items-center rounded-full text-foreground/60 transition-[color,background-color,transform] duration-150 hover:bg-muted hover:text-foreground active:scale-[0.9]",
          className,
        )}
      >
        <BellIcon className="size-5" aria-hidden />
        {unread > 0 && (
          <span className="absolute right-1 top-1 grid min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] font-semibold leading-4 text-white tabular">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={8} align="start" className="z-50 outline-none">
          <Popover.Popup className="w-[min(360px,calc(100vw-2rem))] origin-[var(--transform-origin)] overflow-hidden rounded-2xl border border-border bg-background shadow-lg outline-none transition-[opacity,transform] duration-150 data-[ending-style]:scale-95 data-[ending-style]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0">
            <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
              <Popover.Title className="text-[13px] font-semibold">Notifications</Popover.Title>
              {unread > 0 && (
                <button
                  type="button"
                  onClick={() => markRead.mutate({ all: true })}
                  className="text-xs font-semibold text-accent hover:underline"
                >
                  Mark all read
                </button>
              )}
            </div>
            <div className="max-h-[420px] overflow-y-auto">
              {items.length === 0 ? (
                <p className="px-3 py-8 text-center text-[13px] text-foreground/50">
                  {q.isError ? "Couldn’t load notifications." : "You’re all caught up."}
                </p>
              ) : (
                <NotificationItems items={items} compact onOpen={() => setOpen(false)} />
              )}
            </div>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
