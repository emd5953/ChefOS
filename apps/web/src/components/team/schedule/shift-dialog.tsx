"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage, isApiError } from "@/lib/api";
import { addDays, zonedIso, zonedTime } from "@/lib/dates";
import { formatDate, formatWeekday } from "@/lib/format";
import { useLocationScope } from "@/lib/location-scope";
import { keys, usePositions, useStations } from "@/lib/queries";
import type { Conflict, ScheduleResponse, Shift, ShiftIn } from "@/lib/types";

export type ShiftTarget =
  | { mode: "new"; employeeId: string | null; date: string }
  | { mode: "edit"; shift: Shift };

const CONFLICT_COPY: Record<string, string> = {
  overlap: "Overlap",
  time_off_approved: "Time off",
  time_off_pending: "Time off requested",
  unavailable: "Unavailable",
  inactive_employee: "Inactive",
  not_assigned_to_location: "Other location",
  unassigned: "Unassigned",
};
export const conflictLabel = (k: string) => CONFLICT_COPY[k] ?? k;

export function ShiftDialog({
  target,
  data,
  onClose,
}: {
  target: ShiftTarget | null;
  data: ScheduleResponse;
  onClose: () => void;
}) {
  const key = target ? (target.mode === "edit" ? target.shift.id : `new:${target.employeeId}:${target.date}`) : "none";
  return (
    <Dialog
      open={target !== null}
      onOpenChange={(o) => !o && onClose()}
      title={target?.mode === "edit" ? "Edit shift" : "Add shift"}
      className="max-w-lg"
    >
      {target && <ShiftForm key={key} target={target} data={data} onDone={onClose} />}
    </Dialog>
  );
}

function ShiftForm({ target, data, onDone }: { target: ShiftTarget; data: ScheduleResponse; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const positions = usePositions(true);
  const stations = useStations(data.location_id);
  const tz = data.timezone;
  const dayStart = useLocationScope().current?.business_day_start.slice(0, 5) ?? "04:00";
  const week = data.schedule.week_start;
  const editing = target.mode === "edit" ? target.shift : null;
  const conflicts: Conflict[] = editing ? data.conflicts.filter((c) => c.shift_id === editing.id) : [];

  const initialEmployee = editing ? editing.employee_id : target.mode === "new" ? target.employeeId : null;
  const primaryOf = (id: string | null) => data.employees.find((e) => e.id === id)?.primary_position_id ?? "";
  const [employeeId, setEmployeeId] = useState<string>(initialEmployee ?? "");
  const [positionId, setPositionId] = useState<string>(editing?.position_id ?? primaryOf(initialEmployee));
  const [stationId, setStationId] = useState<string>(editing?.station_id ?? "");
  const [date, setDate] = useState(editing ? editing.business_date : target.mode === "new" ? target.date : week);
  const [start, setStart] = useState(editing ? zonedTime(editing.starts_at, tz) : "16:00");
  const [end, setEnd] = useState(editing ? zonedTime(editing.ends_at, tz) : "22:00");
  const [breakMin, setBreakMin] = useState(String(editing?.break_minutes ?? 0));
  const [notes, setNotes] = useState(editing?.notes ?? "");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: keys.team });
  const save = useMutation({
    mutationFn: (body: ShiftIn) =>
      editing
        ? api<Shift>(`/v1/team/shifts/${editing.id}`, {
            method: "PATCH",
            body: { ...body, expected_updated_at: editing.updated_at },
          })
        : api<Shift>("/v1/team/schedule/shifts", {
            method: "POST",
            body,
            query: { location_id: data.location_id, week_start: week },
          }),
    onSuccess: async () => {
      await invalidate();
      onDone();
    },
    onError: (e) => {
      if (isApiError(e) && Object.keys(e.errors).length) setErrors(e.errors);
      else setFormError(errorMessage(e));
      if (isApiError(e) && e.status === 409 && e.code === "conflict") void invalidate();
    },
  });
  const remove = useMutation({
    mutationFn: () => api<void>(`/v1/team/shifts/${editing?.id}`, { method: "DELETE" }),
    onSuccess: async () => {
      await invalidate();
      toast("Shift deleted.", "success");
      onDone();
    },
    onError: (e) => setFormError(errorMessage(e)),
  });

  const submit = (ev: React.FormEvent) => {
    ev.preventDefault();
    setFormError(null);
    const errs: Record<string, string> = {};
    if (!positionId) errs.position_id = "Choose a position";
    const brk = Number(breakMin || 0);
    if (!Number.isInteger(brk) || brk < 0) errs.break_minutes = "Whole minutes";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    // The day is a business day: a start before the day boundary (e.g. 01:00 with a 04:00
    // start) is on the next calendar date. An end at or before the start runs past midnight.
    const startDate = start < dayStart ? addDays(date, 1) : date;
    const endDate = end <= start ? addDays(startDate, 1) : startDate;
    save.mutate({
      employee_id: employeeId || null,
      position_id: positionId,
      station_id: stationId || null,
      starts_at: zonedIso(startDate, start, tz),
      ends_at: zonedIso(endDate, end, tz),
      break_minutes: brk,
      notes,
    });
  };

  const days = Array.from({ length: 7 }, (_, i) => addDays(week, i));
  const activePositions = (positions.data ?? []).filter((p) => !p.archived_at || p.id === editing?.position_id);
  const roster = data.employees.filter((e) => e.active || e.id === initialEmployee);

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      {formError && (
        <p role="alert" className="rounded-xl bg-destructive/10 px-3.5 py-2.5 text-[13px] text-destructive">
          {formError}
        </p>
      )}
      {conflicts.length > 0 && (
        <ul className="space-y-1 rounded-xl bg-warning/10 px-3.5 py-2.5 text-[13px] text-warning">
          {conflicts.map((c, i) => (
            <li key={i}>
              <span className="font-semibold">{conflictLabel(c.kind)}:</span> {c.detail}
            </li>
          ))}
        </ul>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Employee" error={errors.employee_id}>
          {(p) => (
            <Select
              {...p}
              value={employeeId}
              onChange={(e) => {
                setEmployeeId(e.target.value);
                if (!editing) setPositionId(primaryOf(e.target.value) || positionId);
              }}
            >
              <option value="">Unassigned</option>
              {roster.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.display_name}
                  {e.assigned ? "" : " (other location)"}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Position" error={errors.position_id}>
          {(p) => (
            <Select {...p} value={positionId} onChange={(e) => setPositionId(e.target.value)}>
              <option value="">Choose…</option>
              {activePositions.map((pos) => (
                <option key={pos.id} value={pos.id}>
                  {pos.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
      <div className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3">
        <Field label="Day" error={errors.starts_at}>
          {(p) => (
            <Select {...p} value={date} onChange={(e) => setDate(e.target.value)}>
              {days.map((d) => (
                <option key={d} value={d}>
                  {formatWeekday(d)} {formatDate(d)}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Start">{(p) => <Input {...p} type="time" step={900} value={start} onChange={(e) => setStart(e.target.value)} />}</Field>
        <Field label="End" error={errors.ends_at} hint={end <= start ? "Next day" : undefined}>
          {(p) => <Input {...p} type="time" step={900} value={end} onChange={(e) => setEnd(e.target.value)} />}
        </Field>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Unpaid break (min)" error={errors.break_minutes}>
          {(p) => <Input {...p} inputMode="numeric" value={breakMin} onChange={(e) => setBreakMin(e.target.value)} />}
        </Field>
        <Field label="Station" error={errors.station_id}>
          {(p) => (
            <Select {...p} value={stationId} onChange={(e) => setStationId(e.target.value)}>
              <option value="">None</option>
              {(stations.data ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
      <Field label="Notes" error={errors.notes}>
        {(p) => <Input {...p} value={notes} maxLength={500} onChange={(e) => setNotes(e.target.value)} />}
      </Field>
      <div className="flex items-center justify-between gap-2 pt-2">
        {editing ? (
          <Button variant="ghost" onClick={() => remove.mutate()} loading={remove.isPending}>
            <Trash2 aria-hidden /> Delete
          </Button>
        ) : (
          <span />
        )}
        <div className="flex gap-2">
          <Button variant="outline" onClick={onDone}>
            Cancel
          </Button>
          <Button type="submit" loading={save.isPending}>
            {editing ? "Save shift" : "Add shift"}
          </Button>
        </div>
      </div>
    </form>
  );
}
