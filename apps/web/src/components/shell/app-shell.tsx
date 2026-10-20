"use client";

import { useQuery } from "@tanstack/react-query";
import { Menu as MenuIcon } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Brand } from "@/components/brand";
import { Drawer } from "@/components/ui/dialog";
import { ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { LocationScopeProvider } from "@/lib/location-scope";
import { keys } from "@/lib/queries";
import type { Session } from "@/lib/types";
import { ShellSkeleton } from "./shell-skeleton";
import { Sidebar } from "./sidebar";

export function AppShell({ children }: { children: ReactNode }) {
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
        <ErrorState title="Couldn’t reach your workspace" error={me.error} onRetry={() => void me.refetch()} retrying={me.isFetching} />
      </div>
    );
  }
  if (!me.data || !session) return <ShellSkeleton />;

  return (
    <LocationScopeProvider>
      <Frame>{children}</Frame>
    </LocationScopeProvider>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="min-h-dvh lg:pl-[264px]">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[264px] border-r border-border bg-background lg:block">
        <Sidebar />
      </aside>

      <header className="sticky top-0 z-20 flex h-14 items-center gap-2 border-b border-border bg-background/85 px-3 backdrop-blur-sm lg:hidden">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Open navigation"
          className="relative grid size-10 place-items-center rounded-full text-foreground/70 transition-[color,background-color,transform] duration-150 hover:bg-muted hover:text-foreground active:scale-[0.88]"
        >
          <MenuIcon className="size-5" aria-hidden />
        </button>
        <Brand />
      </header>
      <Drawer open={open} onOpenChange={setOpen} title="Navigation">
        <Sidebar onNavigate={() => setOpen(false)} />
      </Drawer>

      <main id="main" className="mx-auto w-full max-w-[1280px] px-4 pb-20 pt-8 sm:px-8 sm:pt-12">
        {children}
      </main>
    </div>
  );
}
