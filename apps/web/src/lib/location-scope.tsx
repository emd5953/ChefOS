"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { useLocations } from "./queries";
import type { Location } from "./types";

type Scope = {
  /** Active (non-archived) locations. */
  locations: Location[];
  current: Location | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
  setLocation: (id: string) => void;
  /** Build an in-app href that carries the selected location. */
  href: (path: string, params?: Record<string, string | undefined>) => string;
};

const ScopeContext = createContext<Scope | null>(null);

export function LocationScopeProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const q = useLocations();

  const locations = useMemo(() => (q.data ?? []).filter((l) => !l.archived_at), [q.data]);
  const requested = search.get("loc");
  const current = locations.find((l) => l.id === requested) ?? locations[0];
  const currentId = current?.id;

  const setLocation = useCallback(
    (id: string) => {
      const sp = new URLSearchParams(search.toString());
      sp.set("loc", id);
      router.replace(`${pathname}?${sp.toString()}`, { scroll: false });
    },
    [router, pathname, search],
  );

  const href = useCallback(
    (path: string, params?: Record<string, string | undefined>) => {
      const sp = new URLSearchParams();
      if (currentId) sp.set("loc", currentId);
      for (const [k, v] of Object.entries(params ?? {})) if (v) sp.set(k, v);
      const qs = sp.toString();
      return qs ? `${path}?${qs}` : path;
    },
    [currentId],
  );

  const value = useMemo<Scope>(
    () => ({
      locations,
      current,
      isLoading: q.isPending,
      isError: q.isError,
      error: q.error,
      refetch: () => void q.refetch(),
      setLocation,
      href,
    }),
    [locations, current, q, setLocation, href],
  );
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export function useLocationScope(): Scope {
  const ctx = useContext(ScopeContext);
  if (!ctx) throw new Error("useLocationScope must be used inside the app shell");
  return ctx;
}
