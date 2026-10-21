"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type {
  Briefing,
  CompareMode,
  Connection,
  ConnectionDetail,
  DailyResponse,
  Employee,
  Invitation,
  ItemsResponse,
  List,
  Location,
  MappingCandidate,
  MappingIssue,
  MyAvailability,
  NotificationList,
  Position,
  RequestsInbox,
  ScheduleResponse,
  SousStatus,
  Station,
  TimeOff,
  SummaryResponse,
  TodayResponse,
} from "./types";

export const keys = {
  me: ["me"] as const,
  locations: ["locations"] as const,
  connections: ["connections"] as const,
  connection: (id: string) => ["connection", id] as const,
  issues: ["mapping-issues", "open"] as const,
  resolvedIssues: ["mapping-issues", "resolved"] as const,
  candidates: (id: string) => ["mapping-candidates", id] as const,
  today: (loc: string) => ["today", loc] as const,
  summary: (loc: string, from: string, to: string, compare: CompareMode) =>
    ["summary", loc, from, to, compare] as const,
  daily: (loc: string, from: string, to: string) => ["daily", loc, from, to] as const,
  items: (loc: string, from: string, to: string) => ["items", loc, from, to] as const,
  sousStatus: ["sous-status"] as const,
  briefing: (loc: string, date: string) => ["sous-briefing", loc, date] as const,
  employees: (loc: string, inactive: boolean) => ["team", "employees", loc, inactive] as const,
  positions: (archived: boolean) => ["team", "positions", archived] as const,
  stations: (loc: string) => ["team", "stations", loc] as const,
  invitations: ["team", "invitations"] as const,
  schedule: (loc: string, week: string) => ["team", "schedule", loc, week] as const,
  team: ["team"] as const,
  notifications: ["notifications"] as const,
  myWeek: (week: string) => ["me", "week", week] as const,
  myTimeOff: ["me", "time-off"] as const,
  myAvailability: ["me", "availability"] as const,
  requests: (loc: string, status: string) => ["team", "requests", loc, status] as const,
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

/** Resolved issues; the Integrations page shows ignored ones that are still occurring. */
export function useResolvedIssues(interval: number) {
  return useQuery({
    queryKey: keys.resolvedIssues,
    queryFn: async () =>
      (await api<List<MappingIssue>>("/v1/integrations/mapping-issues", { query: { status: "resolved" } }))
        .data,
    refetchInterval: interval,
  });
}

export function useMappingCandidates(issueId: string | undefined) {
  return useQuery({
    queryKey: keys.candidates(issueId ?? ""),
    queryFn: async () =>
      (await api<List<MappingCandidate>>(`/v1/integrations/mapping-issues/${issueId}/candidates`)).data,
    enabled: !!issueId,
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

export function useSousStatus() {
  return useQuery({
    queryKey: keys.sousStatus,
    queryFn: () => api<SousStatus>("/v1/sous/status"),
    retry: false,
    staleTime: 30_000,
  });
}

/** `date` "" means today at the location (the API resolves it). */
export function useBriefing(locationId: string | undefined, date: string) {
  return useQuery({
    queryKey: keys.briefing(locationId ?? "", date),
    queryFn: () =>
      api<Briefing>("/v1/sous/briefing", { query: { location_id: locationId, date: date || undefined } }),
    enabled: !!locationId,
    placeholderData: keepPreviousData,
    refetchInterval: 5 * 60_000,
  });
}

// ---- Team ----

export function useEmployees(locationId: string | undefined, includeInactive = false) {
  return useQuery({
    queryKey: keys.employees(locationId ?? "", includeInactive),
    queryFn: async () =>
      (
        await api<List<Employee>>("/v1/team/employees", {
          query: { location_id: locationId, include_inactive: includeInactive || undefined },
        })
      ).data,
    enabled: !!locationId,
    placeholderData: keepPreviousData,
  });
}

export function usePositions(includeArchived = false) {
  return useQuery({
    queryKey: keys.positions(includeArchived),
    queryFn: async () =>
      (await api<List<Position>>("/v1/team/positions", { query: { include_archived: includeArchived || undefined } }))
        .data,
  });
}

export function useStations(locationId: string | undefined) {
  return useQuery({
    queryKey: keys.stations(locationId ?? ""),
    queryFn: async () => (await api<List<Station>>("/v1/team/stations", { query: { location_id: locationId } })).data,
    enabled: !!locationId,
  });
}

export function useInvitations() {
  return useQuery({
    queryKey: keys.invitations,
    queryFn: async () =>
      (await api<List<Invitation>>("/v1/team/invitations", { query: { status: "pending" } })).data,
  });
}

export function useSchedule(locationId: string | undefined, weekStart: string) {
  return useQuery({
    queryKey: keys.schedule(locationId ?? "", weekStart),
    queryFn: () =>
      api<ScheduleResponse>("/v1/team/schedule", { query: { location_id: locationId, week_start: weekStart } }),
    enabled: !!locationId,
    placeholderData: keepPreviousData,
  });
}

// ---- Notifications ----

/** Polls every 60 s while the tab is visible, and on focus (spec: no push delivery). */
export function useNotifications(enabled = true) {
  return useQuery({
    queryKey: keys.notifications,
    queryFn: () => api<NotificationList>("/v1/notifications"),
    enabled,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    retry: false,
  });
}

// ---- Availability and time off ----

export function useMyTimeOff() {
  return useQuery({
    queryKey: keys.myTimeOff,
    queryFn: async () => (await api<List<TimeOff>>("/v1/me/time-off")).data,
  });
}

export function useMyAvailability() {
  return useQuery({ queryKey: keys.myAvailability, queryFn: () => api<MyAvailability>("/v1/me/availability") });
}

export function useRequestsInbox(locationId: string | undefined, status: "pending" | "history") {
  return useQuery({
    queryKey: keys.requests(locationId ?? "", status),
    queryFn: () => api<RequestsInbox>("/v1/team/requests", { query: { location_id: locationId, status } }),
    enabled: !!locationId,
    refetchInterval: status === "pending" ? 60_000 : false,
  });
}
