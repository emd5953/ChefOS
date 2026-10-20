"use client";

import { useQuery } from "@tanstack/react-query";
import { Bell, CalendarDays, LayoutDashboard, LogOut, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { Brand } from "@/components/brand";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { keys, useNotifications } from "@/lib/queries";
import type { Session } from "@/lib/types";
import { useLogout } from "@/lib/use-logout";

type Tab = { label: string; path: string; icon: LucideIcon };

const TABS: Tab[] = [
  { label: "Schedule", path: "/my", icon: CalendarDays },
  { label: "Alerts", path: "/my/alerts", icon: Bell },
];

/** Mobile-first shell for employees (MOB-1, ADR 0004): sized for a 390 px viewport, bottom tabs. */
export function MyShell({ children }: { children: ReactNode }) {
  const { session, setSession } = useAuth();
  const me = useQuery({
    queryKey: keys.me,
    queryFn: () => api<Session>("/v1/auth/me"),
    staleTime: 5 * 60_000,
    retry: false,
  });
  useEffect(() => {
    if (me.data) setSession(me.data);
  }, [me.data, setSession]);

  if (me.isError) {
    return (
      <div className="grid min-h-dvh place-items-center p-6">
        <ErrorState title="Couldn’t load your schedule" error={me.error} onRetry={() => void me.refetch()} retrying={me.isFetching} />
      </div>
    );
  }
  if (!me.data || !session) {
    return (
      <div className="mx-auto max-w-[480px] space-y-4 px-4 py-6" role="status" aria-label="Loading">
        <Skeleton className="h-8 w-28" />
        <Skeleton className="h-24 w-full rounded-card" />
        <Skeleton className="h-24 w-full rounded-card" />
      </div>
    );
  }
  return <Frame session={session}>{children}</Frame>;
}

function Frame({ session, children }: { session: Session; children: ReactNode }) {
  const pathname = usePathname();
  const logout = useLogout();
  const unread = useNotifications().data?.unread_count ?? 0;
  return (
    <div className="mx-auto flex min-h-dvh max-w-[480px] flex-col">
      <header className="sticky top-0 z-20 flex h-14 items-center gap-2 border-b border-border bg-background/90 px-4 backdrop-blur-sm">
        <Brand href="/my" />
        <span className="flex-1" />
        {session.role !== "employee" && (
          <Link
            href="/app"
            aria-label="Back to workspace"
            className="grid size-10 place-items-center rounded-full text-foreground/60 transition-[color,background-color,transform] hover:bg-muted hover:text-foreground active:scale-[0.9]"
          >
            <LayoutDashboard className="size-5" aria-hidden />
          </Link>
        )}
        <button
          type="button"
          onClick={() => void logout()}
          aria-label="Log out"
          className="grid size-10 place-items-center rounded-full text-foreground/60 transition-[color,background-color,transform] hover:bg-muted hover:text-foreground active:scale-[0.9]"
        >
          <LogOut className="size-5" aria-hidden />
        </button>
      </header>
      <main id="main" className="flex-1 px-4 pb-28 pt-5">
        {children}
      </main>
      <nav
        aria-label="Employee"
        className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-sm"
      >
        <div className="mx-auto flex max-w-[480px]">
          {TABS.map((t) => {
            const active = t.path === "/my" ? pathname === "/my" : pathname.startsWith(t.path);
            const Icon = t.icon;
            return (
              <Link
                key={t.path}
                href={t.path}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex h-16 flex-1 flex-col items-center justify-center gap-1 text-[11px] font-semibold transition-[color,transform] active:scale-[0.94]",
                  active ? "text-accent" : "text-foreground/50 hover:text-foreground",
                )}
              >
                <span className="relative">
                  <Icon className="size-5" aria-hidden />
                  {t.path === "/my/alerts" && unread > 0 && (
                    <span className="absolute -right-2 -top-1.5 min-w-4 rounded-full bg-accent px-1 text-center text-[10px] leading-4 text-white tabular">
                      {unread > 9 ? "9+" : unread}
                      <span className="sr-only"> unread</span>
                    </span>
                  )}
                </span>
                {t.label}
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
