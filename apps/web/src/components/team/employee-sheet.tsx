"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { GitMerge, Lock } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, Sheet } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage, isApiError } from "@/lib/api";
import { canManage, useSession } from "@/lib/auth";
import { formatMoney } from "@/lib/format";
import { useLocationScope } from "@/lib/location-scope";
import { keys, usePositions } from "@/lib/queries";
import type { Employee, EmployeePatch, PositionRef } from "@/lib/types";
import { LocationsPicker, PositionsPicker, RateField } from "./employee-form";
import { LoginBadge, centsToInput, parseDollars } from "./shared";

export function EmployeeSheet({
  employee,
  others,
  onClose,
  footer,
}: {
  employee: Employee | null;
  /** Candidates for "merge into". */
  others: Employee[];
  onClose: () => void;
  /** Extra actions (the invitation panel). */
  footer?: (e: Employee) => React.ReactNode;
}) {
  return (
    <Sheet
      open={employee !== null}
      onOpenChange={(o) => !o && onClose()}
      title={employee?.display_name ?? "Employee"}
      description={
        employee ? (employee.source === "square" ? "From Square. Name, rate, and status sync from the POS." : "Added in ChefOS.") : undefined
      }
    >
      {employee && (
        <EmployeeDetail key={`${employee.id}:${employee.updated_at}`} employee={employee} others={others} onDone={onClose} footer={footer} />
      )}
    </Sheet>
  );
}

function EmployeeDetail({
  employee,
  others,
  onDone,
  footer,
}: {
  employee: Employee;
  others: Employee[];
  onDone: () => void;
  footer?: (e: Employee) => React.ReactNode;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const { role } = useSession();
  const scope = useLocationScope();
  const positions = usePositions();
  const manual = employee.source === "manual";

  const [name, setName] = useState(employee.display_name);
  const [email, setEmail] = useState(employee.email ?? "");
  const [phone, setPhone] = useState(employee.phone ?? "");
  const [rate, setRate] = useState(centsToInput(employee.rate_override));
  const [locs, setLocs] = useState(employee.location_ids);
  const [pos, setPos] = useState<PositionRef[]>(
    employee.positions.map((p) => ({ position_id: p.id, is_primary: p.is_primary })),
  );
  const [errors, setErrors] = useState<Record<string, string>>({});

  const save = useMutation({
    mutationFn: (body: EmployeePatch) => api<Employee>(`/v1/team/employees/${employee.id}`, { method: "PATCH", body }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.team });
      toast("Employee updated.", "success");
      onDone();
    },
    onError: (e) => {
      if (isApiError(e) && Object.keys(e.errors).length) setErrors(e.errors);
      else toast(errorMessage(e), "error");
    },
  });

  const submit = () => {
    const cents = parseDollars(rate);
    if (Number.isNaN(cents)) return setErrors({ rate_override: "Use dollars, like 18.50" });
    if (locs.length === 0) return setErrors({ location_ids: "Choose at least one location" });
    setErrors({});
    const body: EmployeePatch = {
      email: email.trim() || null,
      phone: phone.trim() || null,
      rate_override: cents,
      location_ids: locs,
      positions: pos,
    };
    if (manual) body.display_name = name;
    save.mutate(body);
  };

  const toggleActive = useMutation({
    mutationFn: () =>
      api<Employee>(`/v1/team/employees/${employee.id}`, { method: "PATCH", body: { active: !employee.active } }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.team });
      toast(employee.active ? "Employee deactivated." : "Employee reactivated.", "success");
      onDone();
    },
    onError: (e) => toast(errorMessage(e), "error"),
  });

  return (
    <div className="space-y-6 pb-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={employee.source === "square" ? "accent" : "neutral"}>
          {employee.source === "square" ? "From Square" : "Manual"}
        </Badge>
        <LoginBadge status={employee.login} />
        {!employee.active && <Badge tone="warning">Inactive</Badge>}
      </div>

      {!manual && (
        <section aria-label="Synced from Square" className="rounded-2xl bg-muted px-4 py-3">
          <p className="eyebrow flex items-center gap-1.5 text-[10px]">
            <Lock className="size-3" aria-hidden /> Synced from Square
          </p>
          <dl className="mt-2 grid grid-cols-2 gap-3 text-[13px]">
            <div>
              <dt className="text-foreground/50">Job title</dt>
              <dd className="font-medium">{employee.job_title ?? "None"}</dd>
            </div>
            <div>
              <dt className="text-foreground/50">POS rate</dt>
              <dd className="font-medium tabular">
                {employee.pos_hourly_rate == null ? "No rate" : `${formatMoney(employee.pos_hourly_rate, 2)}/h`}
              </dd>
            </div>
          </dl>
        </section>
      )}

      <div className="space-y-4">
        {manual && (
          <Field label="Name" error={errors.display_name}>
            {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} />}
          </Field>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Email" error={errors.email}>
            {(p) => <Input {...p} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />}
          </Field>
          <Field label="Phone" error={errors.phone}>
            {(p) => <Input {...p} type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />}
          </Field>
        </div>
        <RateField
          value={rate}
          onChange={setRate}
          error={errors.rate_override}
          hint={manual ? "Used for projected labor cost." : "Overrides the POS rate in projections. Leave empty to use the POS rate."}
          label={manual ? "Hourly rate ($/h)" : "Rate override ($/h)"}
        />
        <PositionsPicker positions={positions.data ?? []} value={pos} onChange={setPos} error={errors.positions} />
        <LocationsPicker locations={scope.locations} value={locs} onChange={setLocs} error={errors.location_ids} />
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" onClick={onDone}>
            Cancel
          </Button>
          <Button onClick={submit} loading={save.isPending}>
            Save changes
          </Button>
        </div>
      </div>

      {footer?.(employee)}

      <div className="space-y-3 border-t border-border pt-5">
        {manual && (
          <Button variant="outline" size="sm" onClick={() => toggleActive.mutate()} loading={toggleActive.isPending}>
            {employee.active ? "Deactivate" : "Reactivate"}
          </Button>
        )}
        {canManage(role) && <MergePanel employee={employee} others={others} onDone={onDone} />}
      </div>
    </div>
  );
}

function MergePanel({ employee, others, onDone }: { employee: Employee; others: Employee[]; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [into, setInto] = useState("");
  const [confirming, setConfirming] = useState(false);
  const target = others.find((o) => o.id === into);
  const merge = useMutation({
    mutationFn: () =>
      api<Employee>(`/v1/team/employees/${employee.id}/merge`, { method: "POST", body: { into_id: into } }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.team });
      toast(`Merged into ${target?.display_name ?? "the other record"}.`, "success");
      setConfirming(false);
      onDone();
    },
    onError: (e) => toast(errorMessage(e), "error"),
  });
  const candidates = others.filter((o) => o.id !== employee.id && !o.merged_into_id);

  return (
    <section aria-label="Merge duplicate">
      <p className="text-[12px] font-semibold text-foreground/80">Duplicate record?</p>
      <p className="mt-1 text-xs text-foreground/50">
        Merge moves timecards, shifts, requests, the POS link, and the login into the other record, then deactivates this one.
      </p>
      <div className="mt-3 flex gap-2">
        <div className="min-w-0 flex-1">
          <Select aria-label="Merge into" value={into} onChange={(e) => setInto(e.target.value)} className="h-10">
            <option value="">Merge into…</option>
            {candidates.map((o) => (
              <option key={o.id} value={o.id}>
                {o.display_name} ({o.source === "square" ? "Square" : "manual"})
              </option>
            ))}
          </Select>
        </div>
        <Button variant="outline" disabled={!into} onClick={() => setConfirming(true)}>
          <GitMerge aria-hidden /> Merge
        </Button>
      </div>
      <Dialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Merge ${employee.display_name} into ${target?.display_name ?? ""}?`}
        description="This can't be undone. The merged record stays in history as inactive."
      >
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setConfirming(false)}>
            Cancel
          </Button>
          <Button variant="destructive" loading={merge.isPending} onClick={() => merge.mutate()}>
            Merge records
          </Button>
        </div>
      </Dialog>
    </section>
  );
}
