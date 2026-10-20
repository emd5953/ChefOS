"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, Plus, UserPlus, Users } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { PageHeader } from "@/components/page-header";
import { PosGate } from "@/components/pos-gate";
import { Badge } from "@/components/ui/badge";
import { Button, LinkButton } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { TableSkeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage, isApiError } from "@/lib/api";
import { canManage, useSession } from "@/lib/auth";
import { formatMoney } from "@/lib/format";
import { useLocationScope } from "@/lib/location-scope";
import { keys, useEmployees, usePositions } from "@/lib/queries";
import type { Employee, EmployeeCreate, Location, PositionRef } from "@/lib/types";
import { LocationsPicker, PositionsPicker, RateField } from "./employee-form";
import { EmployeeSheet } from "./employee-sheet";
import { InviteManagerDialog, InvitePanel } from "./invite-panel";
import { Positions, Stations } from "./positions";
import { Checkbox, LoginBadge, PositionChip, Tabs, parseDollars } from "./shared";

type Tab = "employees" | "positions";

export function Team() {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const { role } = useSession();
  const { href } = useLocationScope();
  const [invitingManager, setInvitingManager] = useState(false);
  const tab: Tab = search.get("tab") === "positions" ? "positions" : "employees";
  const setTab = (t: Tab) => {
    const sp = new URLSearchParams(search.toString());
    if (t === "employees") sp.delete("tab");
    else sp.set("tab", t);
    router.replace(`${pathname}?${sp.toString()}`, { scroll: false });
  };

  return (
    <>
      <PageHeader
        overline="Team"
        title="Team"
        description="Everyone on the schedule: who they are, what they do, where they work, and whether they can log in."
        actions={
          <>
            {canManage(role) && (
              <Button variant="outline" onClick={() => setInvitingManager(true)}>
                <UserPlus aria-hidden /> Invite manager
              </Button>
            )}
            <LinkButton href={href("/app/team/schedule")}>
              <CalendarDays aria-hidden /> Schedule
            </LinkButton>
          </>
        }
      />
      <div className="mb-5">
        <Tabs
          label="Team sections"
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "employees", label: "Employees" },
            { id: "positions", label: "Positions" },
          ]}
        />
      </div>
      <InviteManagerDialog open={invitingManager} onClose={() => setInvitingManager(false)} />
      {tab === "positions" ? (
        <>
          <Positions />
          <PosGate requirePos={false}>{(loc) => <Stations location={loc} />}</PosGate>
        </>
      ) : <PosGate requirePos={false}>{(loc) => <Employees location={loc} />}</PosGate>}
    </>
  );
}

function Employees({ location }: { location: Location }) {
  const [inactive, setInactive] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const q = useEmployees(location.id, inactive);
  const rows = q.data ?? [];
  const open = rows.find((e) => e.id === openId) ?? null;

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3 p-5 pb-3 sm:p-6 sm:pb-3">
        <div>
          <h2 className="text-[17px] font-semibold tracking-[-0.03em]">Employees at {location.name}</h2>
          <p className="mt-1 text-[13px] text-foreground/55">
            {q.data ? `${rows.filter((r) => r.active).length} active` : " "}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Checkbox label="Show inactive" checked={inactive} onChange={setInactive} />
          <Button onClick={() => setAdding(true)}>
            <Plus aria-hidden /> Add employee
          </Button>
        </div>
      </div>
      {q.isPending ? (
        <TableSkeleton rows={8} cols={5} />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<Users />}
          title="No employees yet"
          description="Connect Square to import your team, or add one."
          action={<Button onClick={() => setAdding(true)}>Add employee</Button>}
        />
      ) : (
        <div className="overflow-x-auto scrollbar-thin" role="region" aria-label="Employees" tabIndex={0}>
          <table className="w-full min-w-[720px] border-separate border-spacing-0 text-[13px]">
            <thead>
              <tr>
                {["Name", "Positions", "Source", "Rate", "Login"].map((h, i) => (
                  <th
                    key={h}
                    scope="col"
                    className={`eyebrow border-b border-border px-3 py-2 text-left text-[10px] first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6 ${i === 3 ? "text-right" : ""}`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <EmployeeRow key={e.id} e={e} onOpen={() => setOpenId(e.id)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <EmployeeSheet
        employee={open}
        others={rows}
        onClose={() => setOpenId(null)}
        footer={(e) => <InvitePanel employee={e} />}
      />
      <AddEmployeeDialog open={adding} onClose={() => setAdding(false)} location={location} />
    </Card>
  );
}

function EmployeeRow({ e, onOpen }: { e: Employee; onOpen: () => void }) {
  const cell = "border-b border-border px-3 py-3 first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6";
  return (
    <tr className="group cursor-pointer transition-colors hover:bg-muted/60" onClick={onOpen}>
      <td className={cell}>
        <button type="button" onClick={onOpen} className="text-left font-semibold tracking-[-0.01em] underline-offset-4 group-hover:underline">
          {e.display_name}
        </button>
        <p className="text-xs text-foreground/45">{e.email ?? "No email"}</p>
      </td>
      <td className={cell}>
        <div className="flex flex-wrap gap-1">
          {e.positions.length ? e.positions.map((p) => <PositionChip key={p.id} position={p} />) : <span className="text-foreground/40">None</span>}
        </div>
      </td>
      <td className={cell}>
        <div className="flex flex-wrap gap-1">
          <Badge tone={e.source === "square" ? "accent" : "neutral"}>{e.source === "square" ? "From Square" : "Manual"}</Badge>
          {!e.active && <Badge tone="warning">{e.merged_into_id ? "Merged" : "Inactive"}</Badge>}
        </div>
      </td>
      <td className={`${cell} text-right tabular`}>
        {e.effective_rate == null ? (
          <span className="text-warning">No rate</span>
        ) : (
          <>
            {formatMoney(e.effective_rate, 2)}
            <span className="text-foreground/40">/h</span>
            {e.rate_override != null && e.source === "square" && <p className="text-[11px] text-foreground/45">override</p>}
          </>
        )}
      </td>
      <td className={cell}>
        <LoginBadge status={e.login} />
      </td>
    </tr>
  );
}

function AddEmployeeDialog({ open, onClose, location }: { open: boolean; onClose: () => void; location: Location }) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="Add employee" description="For staff who aren’t in your POS.">
      {open && <AddEmployeeForm location={location} onDone={onClose} />}
    </Dialog>
  );
}

function AddEmployeeForm({ location, onDone }: { location: Location; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const scope = useLocationScope();
  const positions = usePositions();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [rate, setRate] = useState("");
  const [locs, setLocs] = useState([location.id]);
  const [pos, setPos] = useState<PositionRef[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: (body: EmployeeCreate) => api<Employee>("/v1/team/employees", { method: "POST", body }),
    onSuccess: async (e) => {
      await qc.invalidateQueries({ queryKey: keys.team });
      toast(`${e.display_name} added.`, "success");
      onDone();
    },
    onError: (e) => {
      if (isApiError(e) && Object.keys(e.errors).length) setErrors(e.errors);
      else setFormError(errorMessage(e));
    },
  });

  const submit = (ev: React.FormEvent) => {
    ev.preventDefault();
    const cents = parseDollars(rate);
    const errs: Record<string, string> = {};
    if (!name.trim()) errs.display_name = "Enter a name";
    if (Number.isNaN(cents)) errs.rate_override = "Use dollars, like 18.50";
    if (!locs.length) errs.location_ids = "Choose at least one location";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    create.mutate({
      display_name: name,
      email: email.trim() || null,
      phone: phone.trim() || null,
      rate_override: cents,
      location_ids: locs,
      positions: pos,
    });
  };

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      {formError && (
        <p role="alert" className="rounded-xl bg-destructive/10 px-3.5 py-2.5 text-[13px] text-destructive">
          {formError}
        </p>
      )}
      <Field label="Name" error={errors.display_name}>
        {(p) => <Input {...p} autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Jordan Reyes" />}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Email" error={errors.email} hint="Needed to invite them to log in">
          {(p) => <Input {...p} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />}
        </Field>
        <Field label="Phone" error={errors.phone}>
          {(p) => <Input {...p} type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />}
        </Field>
      </div>
      <RateField label="Hourly rate ($/h)" value={rate} onChange={setRate} error={errors.rate_override} />
      <PositionsPicker positions={positions.data ?? []} value={pos} onChange={setPos} error={errors.positions} />
      <LocationsPicker locations={scope.locations} value={locs} onChange={setLocs} error={errors.location_ids} />
      <div className="flex justify-end gap-2 pt-2">
        <Button variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" loading={create.isPending}>
          Add employee
        </Button>
      </div>
    </form>
  );
}
