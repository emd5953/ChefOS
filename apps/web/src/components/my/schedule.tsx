"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, MapPin, WifiOff } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { api, isApiError } from "@/lib/api";
import { useSession } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { addDays, mondayOf, todayIn } from "@/lib/dates";
import { formatClock, formatDate, formatDateTime, formatShiftRange, formatWeekday } from "@/lib/format";
import { loadMyWeek, saveMyWeek } from "@/lib/offline";
import { keys } from "@/lib/queries";
import type { MyWeek, OpenShift as OpenShiftType, TeamShift } from "@/lib/types";

/** True while the browser reports no connection. */
export function useOnline(): boolean {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  return online;
}

function useWeekParam(): [string, (w: string) => void, string] {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const thisWeek = mondayOf(todayIn());
  const requested = search.get("week");
  const week = requested && /^\d{4}-\d{2}-\d{2}$/.test(requested) ? mondayOf(requested) : thisWeek;
  const setWeek = (w: string) => router.replace(`${pathname}?week=${w}`, { scroll: false });
  return [week, setWeek, thisWeek];
}

export function MySchedule() {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const [week, setWeek, thisWeek] = useWeekParam();
  const online = useOnline();
  const q = useQuery({
    queryKey: keys.myWeek(week),
    queryFn: async () => {
      const data = await api<MyWeek>("/v1/me/week", { query: { week_start: week } });
      saveMyWeek(user.id, week, data);
      return data;
    },
    retry: false,
    refetchOnWindowFocus: true,
  });
  // Offline (or the API unreachable): fall back to the last copy saved on this device.
  const unreachable = !online || (isApiError(q.error) && q.error.code === "unavailable");
  const cached = useMemo(() => (unreachable ? loadMyWeek(user.id, week) : null), [unreachable, user.id, week]);
  const data = q.data && !unreachable ? q.data : (cached?.data ?? q.data);
  const openShifts = useQuery({
    queryKey: ["me", "open-shifts"],
    queryFn: async () => (await api<{ data: OpenShiftType[] }>("/v1/me/open-shifts")).data,
    enabled: online,
  });
  const claim = useMutation({
    mutationFn: (shiftId: string) => api("/v1/me/shift-coverage-requests", {
      method: "POST", body: { published_shift_id: shiftId },
    }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["me", "open-shifts"] });
    },
  });
  const cancelClaim = useMutation({
    mutationFn: (requestId: string) => api(`/v1/me/shift-coverage-requests/${requestId}/cancel`, { method: "POST" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["me", "open-shifts"] }),
  });

  const weeks = [
    { id: thisWeek, label: "This week" },
    { id: addDays(thisWeek, 7), label: "Next week" },
  ];

  return (
    <>
      <p className="eyebrow">Hi, {user.display_name.split(" ")[0]}</p>
      <h1 className="mt-2 text-[30px] font-semibold leading-tight tracking-[-0.05em]">Your schedule</h1>

      <div role="tablist" aria-label="Week" className="mt-5 grid grid-cols-2 rounded-full border border-border p-1">
        {weeks.map((w) => (
          <button
            key={w.id}
            type="button"
            role="tab"
            aria-selected={week === w.id}
            onClick={() => setWeek(w.id)}
            className={cn(
              "h-9 rounded-full text-[13px] font-semibold transition-[color,background-color,transform] active:scale-[0.97]",
              week === w.id ? "bg-foreground text-background" : "text-foreground/55",
            )}
          >
            {w.label}
          </button>
        ))}
      </div>
      <p className="mt-2 text-center text-xs text-foreground/45">
        {formatDate(week)} – {formatDate(addDays(week, 6))}
      </p>

      {online && openShifts.data && openShifts.data.length > 0 && (
        <section aria-labelledby="open-shifts-heading" className="mt-5 space-y-2">
          <h2 id="open-shifts-heading" className="text-[17px] font-semibold tracking-[-0.03em]">Open shifts</h2>
          {openShifts.data.map((shift) => (
            <Card key={shift.id} className="flex items-center gap-3 p-4">
              <span className="size-1.5 shrink-0 rounded-full" style={{ backgroundColor: shift.position_color }} aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="font-semibold">{formatWeekday(shift.business_date)}, {formatDate(shift.business_date)}</p>
                <p className="text-sm text-foreground/60">{formatShiftRange(shift.starts_at, shift.ends_at, shift.timezone)} · {shift.position_name}</p>
                <p className="text-xs text-foreground/45">{shift.location_name}{shift.station_name ? ` · ${shift.station_name}` : ""}</p>
                {shift.request_status === "pending" && <p className="mt-1 text-xs font-medium text-accent">Request sent · awaiting manager</p>}
                {shift.request_status === "denied" && <p className="mt-1 text-xs text-foreground/50">Your request was declined</p>}
              </div>
              {shift.request_status === "pending" ? (
                <Button size="sm" variant="ghost" loading={cancelClaim.isPending} onClick={() => cancelClaim.mutate(shift.request_id ?? "")}>
                  Cancel
                </Button>
              ) : (
                <Button size="sm" variant="outline" loading={claim.isPending} onClick={() => claim.mutate(shift.id)}>
                  Request
                </Button>
              )}
            </Card>
          ))}
          {claim.isError && <p role="alert" className="text-sm text-destructive">{claim.error instanceof Error ? claim.error.message : "Could not send request."}</p>}
          {cancelClaim.isError && <p role="alert" className="text-sm text-destructive">{cancelClaim.error instanceof Error ? cancelClaim.error.message : "Could not cancel request."}</p>}
        </section>
      )}

      {unreachable && (
        <p role="status" className="mt-4 flex items-center gap-2 rounded-xl bg-warning/10 px-3.5 py-2.5 text-[13px] text-warning">
          <WifiOff className="size-4 shrink-0" aria-hidden />
          {cached ? `Offline, showing schedule as of ${formatClock(cached.savedAt)}` : "Offline. This week hasn’t been saved on this device."}
        </p>
      )}

      <div className="mt-4">
        {data ? (
          <WeekView data={data} />
        ) : q.isPending && !unreachable ? (
          <div className="space-y-3" role="status" aria-label="Loading schedule">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-24 w-full rounded-card" />
            ))}
          </div>
        ) : isApiError(q.error) && q.error.status === 404 ? (
          <Card>
            <EmptyState
              icon={<CalendarDays />}
              title="No employee profile"
              description="Your login isn’t linked to an employee record yet. Ask your manager."
            />
          </Card>
        ) : q.isError && !unreachable ? (
          <Card>
            <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
          </Card>
        ) : null}
      </div>
    </>
  );
}

function WeekView({ data }: { data: MyWeek }) {
  const locs = new Map(data.locations.map((l) => [l.id, l]));
  if (!data.published_at) {
    return (
      <Card>
        <EmptyState
          icon={<CalendarDays />}
          title="No published schedule yet"
          description="When your manager publishes this week, your shifts show up here."
        />
      </Card>
    );
  }
  const teamByDay = new Map<string, TeamShift[]>();
  for (const t of data.team_shifts) teamByDay.set(t.business_date, [...(teamByDay.get(t.business_date) ?? []), t]);
  const multiLocation = new Set(data.my_shifts.map((s) => s.location_id)).size > 1;
  const tzOf = (locationId: string) => locs.get(locationId)?.timezone;

  return (
    <div className="space-y-3">
      <p className="text-xs text-foreground/45">Published {formatDateTime(data.published_at, tzOf(data.locations[0]?.id ?? ""))}</p>
      {data.my_shifts.length === 0 ? (
        <Card>
          <EmptyState icon={<CalendarDays />} title="You’re off this week" description="No shifts for you in the published schedule." />
        </Card>
      ) : (
        data.my_shifts.map((s) => {
          const tz = tzOf(s.location_id) ?? "America/New_York";
          const mates = (teamByDay.get(s.business_date) ?? []).filter(
            (t) => t.location_id === s.location_id && t.starts_at < s.ends_at && t.ends_at > s.starts_at,
          );
          return (
            <Card key={s.id} className="overflow-hidden">
              <div className="flex gap-4 p-4" style={{ boxShadow: `inset 4px 0 0 ${s.position_color}` }}>
                <div className="w-12 shrink-0 text-center">
                  <p className="eyebrow text-[10px]">{formatWeekday(s.business_date)}</p>
                  <p className="text-[22px] font-semibold leading-tight tracking-[-0.04em] tabular">{s.business_date.slice(8)}</p>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-[17px] font-semibold tracking-[-0.03em] tabular">{formatShiftRange(s.starts_at, s.ends_at, tz)}</p>
                  <p className="text-[13px] text-foreground/60">
                    {s.position_name}
                    {s.station_name ? ` · ${s.station_name}` : ""}
                    {s.break_minutes ? ` · ${s.break_minutes} min break` : ""}
                  </p>
                  {multiLocation && (
                    <p className="mt-1 flex items-center gap-1 text-xs text-foreground/50">
                      <MapPin className="size-3" aria-hidden /> {locs.get(s.location_id)?.name}
                    </p>
                  )}
                  {s.notes && <p className="mt-1 text-xs text-foreground/55">{s.notes}</p>}
                </div>
              </div>
              {mates.length > 0 && (
                <div className="border-t border-border bg-muted/60 px-4 py-2.5">
                  <p className="eyebrow text-[10px]">Working with you</p>
                  <ul className="mt-1.5 flex flex-wrap gap-1.5">
                    {mates.map((t, i) => (
                      <li key={i} className="rounded-full bg-background px-2 py-0.5 text-[11px]" title={formatShiftRange(t.starts_at, t.ends_at, tz)}>
                        <span aria-hidden className="mr-1 inline-block size-1.5 rounded-full align-middle" style={{ background: t.position_color }} />
                        {t.employee_name.split(" ")[0]} · {t.position_name}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>
          );
        })
      )}
    </div>
  );
}
