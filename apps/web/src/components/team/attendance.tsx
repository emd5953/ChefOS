"use client";

import { CalendarDays, ChevronLeft, ChevronRight, Clock } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { FreshnessBadge } from "@/components/freshness-badge";
import { PageHeader } from "@/components/page-header";
import { PosGate } from "@/components/pos-gate";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { TableSkeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import { addDays, mondayOf, zonedDate } from "@/lib/dates";
import { formatClock, formatDate, formatDateTime, formatHours, formatShiftRange, formatWeekday } from "@/lib/format";
import { useLocationScope } from "@/lib/location-scope";
import { useAttendance } from "@/lib/queries";
import type { AttendanceDay, AttendanceResponse, AttendanceShift, AttendanceStatus, Location } from "@/lib/types";
import { PositionChip } from "./shared";

export function AttendanceView() {
  const { href } = useLocationScope();
  return (
    <>
      <PageHeader
        overline="Team"
        title="Attendance"
        description="Published shifts against imported timecards. Late means no clock-in 10 minutes after the start; missed means none by the end."
        actions={
          <LinkButton variant="outline" href={href("/app/team/schedule")}>
            <CalendarDays aria-hidden /> Schedule
          </LinkButton>
        }
      />
      <PosGate requirePos={false}>{(loc) => <Attendance location={loc} />}</PosGate>
    </>
  );
}

/** Today's business date: local wall clock shifted back by the day-start time (default 04:00). */
function businessToday(location: Location): string {
  const [h = 4, m = 0] = location.business_day_start.split(":").map(Number);
  return zonedDate(new Date(Date.now() - (h * 60 + m) * 60_000).toISOString(), location.timezone);
}

function useDay(location: Location): [string, string, (d: string) => void] {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const today = businessToday(location);
  const requested = search.get("date");
  const day = requested && /^\d{4}-\d{2}-\d{2}$/.test(requested) ? requested : today;
  const setDay = (d: string) => {
    const sp = new URLSearchParams(search.toString());
    if (d === today) sp.delete("date");
    else sp.set("date", d);
    router.replace(`${pathname}?${sp.toString()}`, { scroll: false });
  };
  return [day, today, setDay];
}

function Attendance({ location }: { location: Location }) {
  const [day, today, setDay] = useDay(location);
  const week = mondayOf(day);
  const q = useAttendance(location.id, week, addDays(week, 6));
  const selected = q.data?.days.find((d) => d.business_date === day);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="icon" aria-label="Previous day" onClick={() => setDay(addDays(day, -1))}>
          <ChevronLeft aria-hidden />
        </Button>
        <p className="min-w-36 text-center text-[15px] font-semibold tracking-[-0.02em] tabular" aria-live="polite">
          {formatWeekday(day)}, {formatDate(day, true)}
        </p>
        <Button variant="outline" size="icon" aria-label="Next day" onClick={() => setDay(addDays(day, 1))}>
          <ChevronRight aria-hidden />
        </Button>
        {day !== today && (
          <Button variant="outline" onClick={() => setDay(today)}>
            Today
          </Button>
        )}
        {q.data && <FreshnessBadge freshness={q.data.freshness} timeZone={location.timezone} className="ml-auto" />}
      </div>

      {q.isPending ? (
        <Card>
          <TableSkeleton rows={6} cols={6} />
        </Card>
      ) : q.isError ? (
        <Card>
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        </Card>
      ) : (
        <>
          <WeekStrip data={q.data} day={day} onPick={setDay} />
          <SourceNote data={q.data} tz={location.timezone} />
          {selected && <DayDetail data={q.data} d={selected} tz={location.timezone} />}
        </>
      )}
    </div>
  );
}

function SourceNote({ data, tz }: { data: AttendanceResponse; tz: string }) {
  const f = data.freshness[0];
  if (data.as_of == null) {
    return (
      <p className="rounded-xl bg-muted px-4 py-3 text-[13px] text-foreground/65">
        No timecards are imported for this location, so shifts can&apos;t be judged late or missed. Connect your POS to
        compare clock-ins.
      </p>
    );
  }
  if (f && f.status !== "fresh") {
    return (
      <p className="rounded-xl bg-warning/10 px-4 py-3 text-[13px] text-warning">
        Timecards are imported through {formatDateTime(data.as_of, tz)}. Shifts due after that show as awaiting
        timecards, not as late.
      </p>
    );
  }
  return null;
}

function WeekStrip({ data, day, onPick }: { data: AttendanceResponse; day: string; onPick: (d: string) => void }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7" role="list" aria-label="Week">
      {data.days.map((d) => {
        const problems = d.counts.late + d.counts.missed;
        return (
          <button
            key={d.business_date}
            type="button"
            role="listitem"
            aria-current={d.business_date === day ? "date" : undefined}
            onClick={() => onPick(d.business_date)}
            className={cn(
              "rounded-card border p-3 text-left transition-[background-color,border-color,transform] duration-150 active:scale-[0.98]",
              d.business_date === day ? "border-foreground bg-foreground/[0.04]" : "border-border hover:bg-muted/60",
            )}
          >
            <p className="eyebrow">
              {formatWeekday(d.business_date)} {formatDate(d.business_date)}
            </p>
            <p className="mt-2 text-[13px] tabular">
              <span className="font-semibold">{d.actual_hours.toFixed(1)}</span>
              <span className="text-foreground/50"> / {d.scheduled_hours.toFixed(1)} h</span>
            </p>
            <p className={cn("mt-1 text-[11px] font-semibold", problems ? "text-warning" : "text-foreground/45")}>
              {problems
                ? [d.counts.late && `${d.counts.late} late`, d.counts.missed && `${d.counts.missed} missed`].filter(Boolean).join(" · ")
                : d.shifts.length
                  ? "No exceptions"
                  : "No shifts"}
            </p>
          </button>
        );
      })}
    </div>
  );
}

function DayDetail({ data, d, tz }: { data: AttendanceResponse; d: AttendanceDay; tz: string }) {
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label="Scheduled hours" value={formatHours(d.scheduled_hours)} note="Published shifts, open ones included" />
        <Tile label="Actual hours" value={formatHours(d.actual_hours)} note="Closed timecards; open ones count at clock-out" />
        <Tile label="Late" value={String(d.counts.late)} warn={d.counts.late > 0} note={`No clock-in ${data.grace_minutes} min after start`} />
        <Tile label="Missed" value={String(d.counts.missed)} warn={d.counts.missed > 0} note="No clock-in by the end of the shift" />
      </div>
      <Card>
        {d.shifts.length === 0 ? (
          <EmptyState
            icon={<Clock />}
            title="No published shifts"
            description="Publish this week's schedule to compare it with clock-ins."
          />
        ) : (
          <div className="overflow-x-auto scrollbar-thin" role="region" aria-label="Shifts" tabIndex={0}>
            <table className="w-full min-w-[760px] border-separate border-spacing-0 text-[13px]">
              <thead>
                <tr>
                  {["Employee", "Position", "Scheduled", "Clocked", "Hours", "Status"].map((h, i) => (
                    <th
                      key={h}
                      scope="col"
                      className={`eyebrow border-b border-border px-3 py-2 text-left text-[10px] first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6 ${i === 4 ? "text-right" : ""}`}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.shifts.map((s) => (
                  <ShiftRow key={s.shift_id} s={s} tz={tz} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data.open_shifts_excluded > 0 && (
          <p className="border-t border-border px-5 py-3 text-xs text-foreground/50 sm:px-6">
            Open shifts have nobody to clock in and are not listed; their hours count as scheduled.
          </p>
        )}
      </Card>
      {d.unscheduled.length > 0 && (
        <Card className="p-5 sm:p-6">
          <h2 className="eyebrow">Clocked in without a shift ({d.unscheduled.length})</h2>
          <ul className="mt-3 divide-y divide-border text-[13px]">
            {d.unscheduled.map((t) => (
              <li key={t.timecard_id} className="flex items-center justify-between gap-3 py-2">
                <span className="font-medium">{t.employee_name ?? "Unlinked team member"}</span>
                <span className="tabular text-foreground/65">
                  {formatClock(t.clock_in, tz)} – {t.clock_out ? formatClock(t.clock_out, tz) : "on the clock"}
                  {t.clock_out && ` · ${formatHours(t.actual_hours)}`}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

const STATUS: Record<AttendanceStatus, { label: string; tone: Tone }> = {
  on_time: { label: "On time", tone: "success" },
  late: { label: "Late", tone: "warning" },
  missed: { label: "Missed", tone: "danger" },
  upcoming: { label: "Upcoming", tone: "neutral" },
  awaiting_data: { label: "Awaiting timecards", tone: "neutral" },
};

function ShiftRow({ s, tz }: { s: AttendanceShift; tz: string }) {
  const cell = "border-b border-border px-3 py-3 first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6";
  const st = STATUS[s.status];
  const open = s.clock_in != null && s.clock_out == null;
  return (
    <tr>
      <td className={cn(cell, "font-medium")}>{s.employee_name ?? "Employee"}</td>
      <td className={cell}>
        <PositionChip position={{ name: s.position_name, color: s.position_color }} />
      </td>
      <td className={cn(cell, "tabular")}>{formatShiftRange(s.starts_at, s.ends_at, tz)}</td>
      <td className={cn(cell, "tabular text-foreground/70")}>
        {s.clock_in ? `${formatClock(s.clock_in, tz)} – ${s.clock_out ? formatClock(s.clock_out, tz) : "on the clock"}` : "—"}
      </td>
      <td className={cn(cell, "text-right tabular")}>
        <span className="font-semibold">{open ? "—" : s.actual_hours.toFixed(1)}</span>
        <span className="text-foreground/50"> / {s.scheduled_hours.toFixed(1)} h</span>
      </td>
      <td className={cell}>
        <Badge tone={st.tone}>
          {st.label}
          {s.status === "late" && s.minutes_late != null && ` · ${s.minutes_late} min`}
        </Badge>
      </td>
    </tr>
  );
}

function Tile({ label, value, note, warn }: { label: string; value: string; note?: string; warn?: boolean }) {
  return (
    <Card className="p-5">
      <p className="eyebrow">{label}</p>
      <p className={cn("mt-3 text-[28px] font-semibold leading-none tracking-[-0.05em] tabular", warn && "text-warning")}>
        {value}
      </p>
      {note && <p className="mt-2 text-xs text-foreground/50">{note}</p>}
    </Card>
  );
}
