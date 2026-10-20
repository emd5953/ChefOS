"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarOff, WifiOff } from "lucide-react";
import { useState } from "react";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage, isApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { todayIn } from "@/lib/dates";
import { formatDate } from "@/lib/format";
import { keys, useMyAvailability, useMyTimeOff } from "@/lib/queries";
import type { AvailabilityBlock, AvailabilitySet, TimeOff, TimeOffIn } from "@/lib/types";
import { useOnline } from "./schedule";

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const TONE: Record<string, Tone> = {
  pending: "accent",
  approved: "success",
  denied: "danger",
  rejected: "danger",
  cancelled: "neutral",
  superseded: "neutral",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={TONE[status] ?? "neutral"}>{status[0]?.toUpperCase() + status.slice(1)}</Badge>;
}

export function timeOffDates(t: Pick<TimeOff, "starts_on" | "ends_on" | "start_time" | "end_time">): string {
  const first = `${formatDate(t.starts_on)}${t.start_time ? ` from ${t.start_time}` : ""}`;
  if (t.starts_on === t.ends_on) return t.end_time ? `${first} until ${t.end_time}` : first;
  return `${first} – ${formatDate(t.ends_on)}${t.end_time ? ` until ${t.end_time}` : ""}`;
}

export function describeBlock(b: AvailabilityBlock): string {
  return `${WEEKDAYS[b.weekday]?.slice(0, 3)} ${b.start_time ? `${b.start_time}–${b.end_time}` : "all day"}`;
}

export function MyRequests() {
  const online = useOnline();
  return (
    <>
      <h1 className="text-[30px] font-semibold leading-tight tracking-[-0.05em]">Requests</h1>
      {!online && (
        <p role="status" className="mt-4 flex items-center gap-2 rounded-xl bg-warning/10 px-3.5 py-2.5 text-[13px] text-warning">
          <WifiOff className="size-4 shrink-0" aria-hidden /> Offline. Requests need a connection.
        </p>
      )}
      <TimeOffSection disabled={!online} />
      <AvailabilitySection disabled={!online} />
    </>
  );
}

function TimeOffSection({ disabled }: { disabled: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useMyTimeOff();
  const today = todayIn();
  const [open, setOpen] = useState(false);
  const [startsOn, setStartsOn] = useState(today);
  const [endsOn, setEndsOn] = useState(today);
  const [partial, setPartial] = useState(false);
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});

  const create = useMutation({
    mutationFn: (body: TimeOffIn) => api<TimeOff>("/v1/me/time-off", { method: "POST", body }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.myTimeOff });
      toast("Time off requested. Your manager will be notified.", "success");
      setOpen(false);
      setReason("");
      setErrors({});
    },
    onError: (e) => {
      if (isApiError(e) && Object.keys(e.errors).length) setErrors(e.errors);
      else toast(errorMessage(e), "error");
    },
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api<TimeOff>(`/v1/me/time-off/${id}/cancel`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.myTimeOff }),
    onError: (e) => toast(errorMessage(e), "error"),
  });

  return (
    <section aria-labelledby="time-off" className="mt-6">
      <div className="flex items-center justify-between">
        <h2 id="time-off" className="text-[17px] font-semibold tracking-[-0.03em]">
          Time off
        </h2>
        {!open && (
          <Button size="sm" onClick={() => setOpen(true)} disabled={disabled}>
            Request time off
          </Button>
        )}
      </div>
      {open && (
        <Card className="mt-3 p-4">
          <form
            noValidate
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate({
                starts_on: startsOn,
                ends_on: endsOn < startsOn ? startsOn : endsOn,
                start_time: partial && startTime ? startTime : null,
                end_time: partial && endTime ? endTime : null,
                reason,
              });
            }}
          >
            <div className="grid grid-cols-2 gap-3">
              <Field label="From" error={errors.starts_on ?? errors._}>
                {(p) => <Input {...p} type="date" min={today} value={startsOn} onChange={(e) => setStartsOn(e.target.value)} />}
              </Field>
              <Field label="To" error={errors.ends_on}>
                {(p) => <Input {...p} type="date" min={startsOn} value={endsOn < startsOn ? startsOn : endsOn} onChange={(e) => setEndsOn(e.target.value)} />}
              </Field>
            </div>
            <label className="flex items-center gap-2 text-[13px]">
              <input type="checkbox" checked={partial} onChange={(e) => setPartial(e.target.checked)} className="size-4" />
              Part of the day
            </label>
            {partial && (
              <div className="grid grid-cols-2 gap-3">
                <Field label="Starting at" hint="On the first day">
                  {(p) => <Input {...p} type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} />}
                </Field>
                <Field label="Until" hint="On the last day">
                  {(p) => <Input {...p} type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} />}
                </Field>
              </div>
            )}
            <Field label="Reason (optional)" error={errors.reason}>
              {(p) => <Input {...p} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />}
            </Field>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" loading={create.isPending} disabled={disabled}>
                Send request
              </Button>
            </div>
          </form>
        </Card>
      )}
      <div className="mt-3">
        {q.isPending ? (
          <Skeleton className="h-20 w-full rounded-card" />
        ) : q.isError ? (
          <Card>
            <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
          </Card>
        ) : q.data.length === 0 ? (
          <Card>
            <EmptyState icon={<CalendarOff />} title="No time off requests" description="Requests and their status show up here." />
          </Card>
        ) : (
          <ul className="space-y-2">
            {q.data.map((t) => (
              <li key={t.id}>
                <Card className="flex items-start justify-between gap-3 p-4">
                  <div className="min-w-0">
                    <p className="text-[14px] font-semibold tracking-[-0.02em]">{timeOffDates(t)}</p>
                    {t.reason && <p className="mt-0.5 text-xs text-foreground/55">{t.reason}</p>}
                    {t.decision_note && <p className="mt-1 text-xs text-foreground/55">Manager: {t.decision_note}</p>}
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-2">
                    <StatusBadge status={t.status} />
                    {(t.status === "pending" || t.status === "approved") && t.ends_on >= today && (
                      <button
                        type="button"
                        disabled={disabled || cancel.isPending}
                        onClick={() => cancel.mutate(t.id)}
                        className="text-xs font-semibold text-foreground/55 hover:text-foreground disabled:opacity-40"
                      >
                        Cancel
                      </button>
                    )}
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

type DayChoice = { mode: "available" | "all_day" | "window"; start: string; end: string };

function choicesFrom(set: AvailabilitySet | null | undefined): DayChoice[] {
  const days: DayChoice[] = WEEKDAYS.map(() => ({ mode: "available", start: "16:00", end: "20:00" }));
  for (const b of set?.blocks ?? []) {
    days[b.weekday] = b.start_time
      ? { mode: "window", start: b.start_time, end: b.end_time ?? "20:00" }
      : { mode: "all_day", start: "16:00", end: "20:00" };
  }
  return days;
}

function AvailabilitySection({ disabled }: { disabled: boolean }) {
  const q = useMyAvailability();
  return (
    <section aria-labelledby="availability" className="mt-8">
      <h2 id="availability" className="text-[17px] font-semibold tracking-[-0.03em]">
        Weekly availability
      </h2>
      <p className="mt-1 text-xs text-foreground/50">Mark when you can’t work. Your manager approves changes.</p>
      <div className="mt-3">
        {q.isPending ? (
          <Skeleton className="h-64 w-full rounded-card" />
        ) : q.isError ? (
          <Card>
            <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
          </Card>
        ) : (
          <AvailabilityEditor key={q.data.pending?.id ?? q.data.current?.id ?? "none"} data={q.data} disabled={disabled} />
        )}
      </div>
    </section>
  );
}

function AvailabilityEditor({ data, disabled }: { data: NonNullable<ReturnType<typeof useMyAvailability>["data"]>; disabled: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [days, setDays] = useState<DayChoice[]>(() => choicesFrom(data.pending ?? data.current));
  const [from, setFrom] = useState(todayIn());
  const [note, setNote] = useState(data.pending?.note ?? "");
  const [error, setError] = useState<string | null>(null);
  const submit = useMutation({
    mutationFn: () => {
      const blocks: AvailabilityBlock[] = days.flatMap((d, i) =>
        d.mode === "available"
          ? []
          : [{ weekday: i, start_time: d.mode === "window" ? d.start : null, end_time: d.mode === "window" ? d.end : null }],
      );
      return api<AvailabilitySet>("/v1/me/availability", { method: "PUT", body: { effective_from: from, blocks, note } });
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.myAvailability });
      toast("Availability sent for approval.", "success");
      setError(null);
    },
    onError: (e) => setError(isApiError(e) ? Object.values(e.errors)[0] ?? errorMessage(e) : errorMessage(e)),
  });
  const set = (i: number, patch: Partial<DayChoice>) => setDays((xs) => xs.map((d, j) => (j === i ? { ...d, ...patch } : d)));

  return (
    <Card className="p-4">
      <div className="mb-3 flex flex-wrap gap-2 text-xs">
        {data.current && (
          <span className="flex items-center gap-1.5">
            <StatusBadge status="approved" /> since {formatDate(data.current.effective_from)}
          </span>
        )}
        {data.pending && (
          <span className="flex items-center gap-1.5">
            <StatusBadge status="pending" /> from {formatDate(data.pending.effective_from)}
          </span>
        )}
        {!data.current && !data.pending && <span className="text-foreground/50">Nothing submitted yet: you’re treated as available.</span>}
      </div>
      <ul className="divide-y divide-border">
        {WEEKDAYS.map((name, i) => {
          const d = days[i]!;
          return (
            <li key={name} className="py-2.5">
              <div className="flex items-center justify-between gap-3">
                <span className="text-[13px] font-semibold">{name}</span>
                <Select
                  aria-label={`${name} availability`}
                  value={d.mode}
                  onChange={(e) => set(i, { mode: e.target.value as DayChoice["mode"] })}
                  className={cn("h-9 w-[168px] text-[13px]", d.mode !== "available" && "text-warning")}
                >
                  <option value="available">Available</option>
                  <option value="all_day">Unavailable all day</option>
                  <option value="window">Unavailable from…</option>
                </Select>
              </div>
              {d.mode === "window" && (
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <Input aria-label={`${name} unavailable from`} type="time" value={d.start} onChange={(e) => set(i, { start: e.target.value })} className="h-9" />
                  <Input aria-label={`${name} unavailable until`} type="time" value={d.end} onChange={(e) => set(i, { end: e.target.value })} className="h-9" />
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Field label="Starting">{(p) => <Input {...p} type="date" min={todayIn()} value={from} onChange={(e) => setFrom(e.target.value)} />}</Field>
        <Field label="Note (optional)">{(p) => <Input {...p} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />}</Field>
      </div>
      {error && (
        <p role="alert" className="mt-3 text-xs text-destructive">
          {error}
        </p>
      )}
      <Button className="mt-4 w-full" onClick={() => submit.mutate()} loading={submit.isPending} disabled={disabled}>
        {data.pending ? "Replace pending request" : "Submit for approval"}
      </Button>
    </Card>
  );
}
