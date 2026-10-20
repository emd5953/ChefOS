"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type {
  CompareMode,
  Connection,
  ConnectionDetail,
  DailyResponse,
  ItemsResponse,
  List,
  Location,
  MappingIssue,
  SummaryResponse,
  TodayResponse,
} from "./types";

export const keys = {
  me: ["me"] as const,
  locations: ["locations"] as const,
  connections: ["connections"] as const,
  connection: (id: string) => ["connection", id] as const,
  issues: ["mapping-issues", "open"] as const,
  today: (loc: string) => ["today", loc] as const,
  summary: (loc: string, from: string, to: string, compare: CompareMode) =>
    ["summary", loc, from, to, compare] as const,
  daily: (loc: string, from: string, to: string) => ["daily", loc, from, to] as const,
  items: (loc: string, from: string, to: string) => ["items", loc, from, to] as const,
};

export function useLocations() {
  return useQuery({
    queryKey: keys.locations,
    queryFn: async () => (await api<List<Location>>("/v1/locations")).data,
  });
}

export function useConnections(refetchInterval?: number | false) {
  return useQuery({
    queryKey: keys.connections,
    queryFn: async () => (await api<List<Connection>>("/v1/integrations/connections")).data,
    refetchInterval,
  });
}

/** True while any backfill is incomplete or a sync run is running. */
export function isBusy(c: ConnectionDetail | undefined): boolean {
  if (!c) return false;
  const backfilling = c.streams.some(
    (s) =>
      s.backfill_target_from != null &&
      (s.backfill_completed_through == null || s.backfill_completed_through > s.backfill_target_from),
  );
  return backfilling || c.recent_runs.some((r) => r.status === "running");
}

export function useConnectionDetail(id: string | undefined) {
  return useQuery({
    queryKey: keys.connection(id ?? ""),
    queryFn: () => api<ConnectionDetail>(`/v1/integrations/connections/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (isBusy(q.state.data) ? 3000 : 30_000),
  });
}

export function useMappingIssues(interval: number) {
  return useQuery({
    queryKey: keys.issues,
    queryFn: async () =>
      (await api<List<MappingIssue>>("/v1/integrations/mapping-issues", { query: { status: "open" } }))
        .data,
    refetchInterval: interval,
  });
}

export function useToday(locationId: string | undefined, poll = false) {
  return useQuery({
    queryKey: keys.today(locationId ?? ""),
    queryFn: () => api<TodayResponse>("/v1/commerce/today", { query: { location_id: locationId } }),
    enabled: !!locationId,
    refetchInterval: poll ? 30_000 : false,
  });
}

export function useSummary(
  locationId: string | undefined,
  from: string,
  to: string,
  compare: CompareMode,
) {
  return useQuery({
    queryKey: keys.summary(locationId ?? "", from, to, compare),
    queryFn: () =>
      api<SummaryResponse>("/v1/commerce/summary", {
        query: { location_id: locationId, from, to, compare },
      }),
    enabled: !!locationId,
    placeholderData: keepPreviousData,
  });
}

export function useDaily(
  locationId: string | undefined,
  from: string,
  to: string,
  enabled = true,
) {
  return useQuery({
    queryKey: keys.daily(locationId ?? "", from, to),
    queryFn: () =>
      api<DailyResponse>("/v1/commerce/daily", { query: { location_id: locationId, from, to } }),
    enabled: !!locationId && enabled,
    placeholderData: keepPreviousData,
  });
}

export function useItems(locationId: string | undefined, from: string, to: string) {
  return useQuery({
    queryKey: keys.items(locationId ?? "", from, to),
    queryFn: () =>
      api<ItemsResponse>("/v1/commerce/items", {
        query: { location_id: locationId, from, to, limit: 50 },
      }),
    enabled: !!locationId,
    placeholderData: keepPreviousData,
  });
}
