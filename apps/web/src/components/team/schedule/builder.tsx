"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, ChevronLeft, ChevronRight, Copy, Plus, TriangleAlert } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { FreshnessBadge } from "@/components/freshness-badge";
import { PageHeader } from "@/components/page-header";
import { PosGate } from "@/components/pos-gate";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { addDays, mondayOf, todayIn } from "@/lib/dates";
import { formatDate, formatHours, formatMoney, formatPct, formatShiftRange, formatWeekday } from "@/lib/format";
import { keys, usePositions, useSchedule } from "@/lib/queries";
import type { Conflict, Location, Position, ScheduleDay, ScheduleResponse, Shift } from "@/lib/types";
import { PublishBar } from "./publish-bar";
import { ShiftDialog, conflictLabel, type ShiftTarget } from "./shift-dialog";

export function ScheduleBuilder() {
  return (
    <>
      <PageHeader
        overline="Team"
        title="Schedule"
        description="Build the week against forecast sales. Employees see it only after you publish."
      />
      <PosGate requirePos={false}>{(loc) => <Week location={loc} />}</PosGate>
    </>
  );
}

function useWeek(location: Location): [string, (w: string) => void] {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const requested = search.get("week");
  const week = requested && /^\d{4}-\d{2}-\d{2}$/.test(requested) ? mondayOf(requested) : mondayOf(todayIn(location.timezone));
  const setWeek = (w: string) => {
    const sp = new URLSearchParams(search.toString());
    sp.set("week", w);
    router.replace(`${pathname}?${sp.toString()}`, { scroll: false });
  };
  return [week, setWeek];
}

function Week({ location }: { location: Location }) {
  const [week, setWeek] = useWeek(location);
  const q = useSchedule(location.id, week);
  const positions = usePositions(true);
  const [target, setTarget] = useState<ShiftTarget | null>(null);
  const thisWeek = mondayOf(todayIn(location.timezone));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Button variant="outline" size="icon" aria-label="Previous week" onClick={() => setWeek(addDays(week, -7))}>
            <ChevronLeft aria-hidden />
          </Button>
          <p className="min-w-[180px] text-center text-[15px] font-semibold tracking-[-0.02em] tabular">
            {formatDate(week)} – {formatDate(addDays(week, 6), true)}
          </p>
          <Button variant="outline" size="icon" aria-label="Next week" onClick={() => setWeek(addDays(week, 7))}>
            <ChevronRight aria-hidden />
          </Button>
          {week !== thisWeek && (
            <Button variant="ghost" size="sm" onClick={() => setWeek(thisWeek)}>
              This week
            </Button>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {q.data && <FreshnessBadge freshness={q.data.freshness} timeZone={location.timezone} />}
          {q.data && q.data.shifts.length === 0 && <CopyLastWeek location={location} week={week} />}
          <Button onClick={() => setTarget({ mode: "new", employeeId: null, date: week })}>
            <Plus aria-hidden /> Add shift
          </Button>
        </div>
      </div>

      {q.isPending ? (
        <div className="space-y-4" role="status" aria-label="Loading schedule">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            {Array.from({ length: 5 }, (_, i) => (
              <Skeleton key={i} className="h-24 rounded-card" />
            ))}
          </div>
          <Skeleton className="h-96 rounded-card" />
        </div>
      ) : q.isError ? (
        <Card>
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        </Card>
      ) : (
        <>
          <WeekTiles data={q.data} />
          <Card className="overflow-hidden">
            {q.data.employees.length === 0 && q.data.shifts.length === 0 ? (
              <EmptyState
                icon={<CalendarDays />}
                title="No employees yet"
                description="Connect Square or add employees on the Team page, then build the week here."
              />
            ) : (
              <Grid data={q.data} positions={positions.data ?? []} onOpen={setTarget} />
            )}
          </Card>
          <PublishBar data={q.data} />
          <ShiftDialog target={target} data={q.data} onClose={() => setTarget(null)} />
        </>
      )}
    </div>
  );
}

function CopyLastWeek({ location, week }: { location: Location; week: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const copy = useMutation({
    mutationFn: () =>
      api<{ copied: number; unassigned: number }>("/v1/team/schedule/copy", {
        method: "POST",
        query: { location_id: location.id, week_start: week },
        body: { from_week_start: addDays(week, -7) },
      }),
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: keys.team });
      toast(
        `Copied ${r.copied} shift${r.copied === 1 ? "" : "s"}${r.unassigned ? `; ${r.unassigned} left unassigned` : ""}.`,
        "success",
      );
    },
    onError: (e) => toast(errorMessage(e), "error"),
  });
  return (
    <Button variant="outline" onClick={() => copy.mutate()} loading={copy.isPending}>
      <Copy aria-hidden /> Copy last week
    </Button>
  );
}

function Tile({ label, value, note, warn }: { label: string; value: string; note?: string; warn?: boolean }) {
  return (
    <Card className="p-5">
      <p className="eyebrow">{label}</p>
      <p className={cn("mt-3 text-[28px] font-semibold leading-none tracking-[-0.05em] tabular", warn && "text-warning")}>{value}</p>
      {note && <p className="mt-2 text-xs text-foreground/50">{note}</p>}
    </Card>
  );
}

function forecastNote(reason: ScheduleDay["forecast_reason"]): string {
  return reason === "not_connected" ? "Connect Square" : reason === "not_enough_history" ? "Not enough history" : "";
}

function WeekTiles({ data }: { data: ScheduleResponse }) {
  const w = data.week;
  const reason = data.days.find((d) => d.forecast_reason)?.forecast_reason ?? null;
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
      <Tile label="Scheduled hours" value={formatHours(w.scheduled_hours)} note={`${data.shifts.length} shifts`} />
      <Tile
        label="Projected cost"
        value={formatMoney(w.projected_cost)}
        note={w.unrated_shifts ? `${w.unrated_shifts} shift${w.unrated_shifts === 1 ? "" : "s"} without a rate` : "All shifts rated"}
        warn={w.unrated_shifts > 0}
      />
      <Tile label="Forecast sales" value={w.forecast_sales == null ? "—" : formatMoney(w.forecast_sales)} note={w.forecast_sales == null ? forecastNote(reason) : "Median of the last 4 same weekdays"} />
      <Tile label="Projected labor %" value={formatPct(w.projected_labor_pct)} note="Projected cost ÷ forecast" />
      <Tile
        label="Actual labor %, last 4 weeks"
        value={formatPct(w.reference_labor_pct)}
        note={`${formatDate(w.reference_period.from)} – ${formatDate(w.reference_period.to)}`}
      />
    </div>
  );
}

type Row = { key: string; label: string; employeeId: string | null; sub?: string; muted?: boolean };

function Grid({
  data,
  positions,
  onOpen,
}: {
  data: ScheduleResponse;
  positions: Position[];
  onOpen: (t: ShiftTarget) => void;
}) {
  const tz = data.timezone;
  const dates = data.days.map((d) => d.business_date);
  const posById = useMemo(() => new Map(positions.map((p) => [p.id, p])), [positions]);
  const conflictsBy = useMemo(() => {
    const m = new Map<string, Conflict[]>();
    for (const c of data.conflicts) if (c.kind !== "unassigned") m.set(c.shift_id, [...(m.get(c.shift_id) ?? []), c]);
    return m;
  }, [data.conflicts]);
  const cells = useMemo(() => {
    const m = new Map<string, Shift[]>();
    for (const s of data.shifts) {
      const k = `${s.employee_id ?? "-"}:${s.business_date}`;
      m.set(k, [...(m.get(k) ?? []), s]);
    }
    return m;
  }, [data.shifts]);

  // Rows: employees grouped by primary position, then the Unassigned row.
  const groups = useMemo(() => {
    const byPos = new Map<string, Row[]>();
    for (const e of data.employees) {
      const pid = e.primary_position_id ?? "";
      const row: Row = {
        key: e.id,
        label: e.display_name,
        employeeId: e.id,
        sub: !e.active ? "Inactive" : !e.assigned ? "Other location" : !e.has_rate ? "No rate" : undefined,
        muted: !e.active,
      };
      byPos.set(pid, [...(byPos.get(pid) ?? []), row]);
    }
    return [...byPos.entries()]
      .map(([pid, rows]) => ({ name: posById.get(pid)?.name ?? "No position", color: posById.get(pid)?.color, rows }))
      .sort((a, b) => (a.name === "No position" ? 1 : b.name === "No position" ? -1 : a.name.localeCompare(b.name)));
  }, [data.employees, posById]);

  const cell = "border-b border-r border-border align-top p-1.5";
  const renderRow = (row: Row) => (
    <tr key={row.key}>
      <th scope="row" className={cn(cell, "sticky left-0 z-10 w-[180px] min-w-[180px] bg-background px-4 py-2.5 text-left font-normal")}>
        <span className={cn("block truncate text-[13px] font-semibold tracking-[-0.01em]", row.muted && "text-foreground/45")}>{row.label}</span>
        {row.sub && <span className="block text-[11px] text-warning">{row.sub}</span>}
      </th>
      {dates.map((d) => {
        const shifts = cells.get(`${row.employeeId ?? "-"}:${d}`) ?? [];
        return (
          <td key={d} className={cn(cell, "group min-w-[132px]")}>
            <div className="flex min-h-14 flex-col gap-1">
              {shifts.map((s) => (
                <ShiftCard
                  key={s.id}
                  shift={s}
                  tz={tz}
                  position={posById.get(s.position_id)}
                  conflicts={conflictsBy.get(s.id) ?? []}
                  onClick={() => onOpen({ mode: "edit", shift: s })}
                />
              ))}
              <button
                type="button"
                onClick={() => onOpen({ mode: "new", employeeId: row.employeeId, date: d })}
                aria-label={`Add shift for ${row.label} on ${formatWeekday(d)}`}
                className="grid h-7 place-items-center rounded-lg text-foreground/30 opacity-0 transition-[opacity,background-color] hover:bg-muted hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
              >
                <Plus className="size-3.5" aria-hidden />
              </button>
            </div>
          </td>
        );
      })}
    </tr>
  );

  return (
    <div className="overflow-x-auto scrollbar-thin" role="region" aria-label="Week schedule" tabIndex={0}>
      <table className="w-full min-w-[1100px] border-separate border-spacing-0 text-[13px]">
        <thead>
          <tr>
            <th scope="col" className="eyebrow sticky left-0 z-20 border-b border-r border-border bg-background px-4 py-3 text-left text-[10px]">
              Employee
            </th>
            {dates.map((d) => (
              <th key={d} scope="col" className="border-b border-r border-border px-3 py-3 text-left">
                <span className="eyebrow block text-[10px]">{formatWeekday(d)}</span>
                <span className="text-[13px] font-semibold tabular">{formatDate(d)}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => [
            <tr key={`g:${g.name}`}>
              <th colSpan={8} scope="colgroup" className="sticky left-0 border-b border-border bg-muted px-4 py-1.5 text-left">
                <span className="inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-foreground/60">
                  <span aria-hidden className="size-2 rounded-full" style={{ background: g.color ?? "var(--muted-foreground)" }} />
                  {g.name}
                </span>
              </th>
            </tr>,
            ...g.rows.map(renderRow),
          ])}
          {renderRow({ key: "unassigned", label: "Unassigned", employeeId: null })}
        </tbody>
        <tfoot>
          <DayFooter days={data.days} />
        </tfoot>
      </table>
    </div>
  );
}

function ShiftCard({
  shift,
  tz,
  position,
  conflicts,
  onClick,
}: {
  shift: Shift;
  tz: string;
  position: Position | undefined;
  conflicts: Conflict[];
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full rounded-lg border border-border bg-background px-2 py-1.5 text-left shadow-[inset_3px_0_0_var(--c)] transition-[border-color,transform] hover:border-foreground/30 active:scale-[0.98]"
      style={{ ["--c" as string]: position?.color ?? "var(--muted-foreground)" }}
    >
      <span className="block text-[12px] font-semibold tabular">{formatShiftRange(shift.starts_at, shift.ends_at, tz)}</span>
      <span className="block truncate text-[11px] text-foreground/55">{position?.name ?? "Position"}</span>
      {conflicts.length > 0 && (
        <span className="mt-1 flex flex-wrap gap-1">
          {conflicts.map((c, i) => (
            <span
              key={i}
              title={c.detail}
              className="inline-flex items-center gap-1 rounded-full bg-warning/12 px-1.5 py-0.5 text-[10px] font-semibold text-warning"
            >
              <TriangleAlert className="size-2.5" aria-hidden />
              {conflictLabel(c.kind)}
              <span className="sr-only">: {c.detail}</span>
            </span>
          ))}
        </span>
      )}
    </button>
  );
}

function DayFooter({ days }: { days: ScheduleDay[] }) {
  const cell = "border-r border-border px-3 py-3 align-top text-[12px]";
  return (
    <tr className="bg-muted/60">
      <th scope="row" className={cn(cell, "sticky left-0 z-10 bg-muted px-4 text-left")}>
        <span className="eyebrow text-[10px]">Day totals</span>
      </th>
      {days.map((d) => {
        const basis = d.forecast_basis.map((b) => formatDate(b)).join(", ");
        return (
          <td key={d.business_date} className={cell}>
            <dl className="space-y-1 tabular">
              <div className="flex justify-between gap-2">
                <dt className="text-foreground/50">Hours</dt>
                <dd className="font-semibold">{d.scheduled_hours.toFixed(1)}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-foreground/50">Cost</dt>
                <dd className="font-semibold">{formatMoney(d.projected_cost, 0)}</dd>
              </div>
              <div className="flex justify-between gap-2" title={d.forecast_sales != null ? `Median of ${basis}` : undefined}>
                <dt className="text-foreground/50">Forecast</dt>
                <dd className="font-semibold">
                  {d.forecast_sales != null ? (
                    formatMoney(d.forecast_sales, 0)
                  ) : (
                    <span className="font-normal text-foreground/45">{forecastNote(d.forecast_reason)}</span>
                  )}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-foreground/50">Labor</dt>
                <dd className="font-semibold">{formatPct(d.projected_labor_pct)}</dd>
              </div>
            </dl>
            {d.unrated_shifts > 0 && (
              <p className="mt-1.5 text-[11px] text-warning">
                {d.unrated_shifts} shift{d.unrated_shifts === 1 ? "" : "s"} {d.unrated_shifts === 1 ? "has" : "have"} no rate
              </p>
            )}
            {d.forecast_sales != null && <p className="sr-only">Forecast basis: {basis}</p>}
          </td>
        );
      })}
    </tr>
  );
}
