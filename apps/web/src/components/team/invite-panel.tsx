"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Send } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage, isApiError } from "@/lib/api";
import { canManage, useSession } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import { useLocationScope } from "@/lib/location-scope";
import { keys, useInvitations } from "@/lib/queries";
import type { Employee, Invitation, InvitationCreated } from "@/lib/types";
import { LocationsPicker } from "./employee-form";

type InviteBody = { employee_id?: string; email?: string; role: "manager" | "employee"; location_ids: string[] };

function useInvite(onCreated: (c: InvitationCreated) => void) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (body: InviteBody) => api<InvitationCreated>("/v1/team/invitations", { method: "POST", body }),
    onSuccess: async (c) => {
      await qc.invalidateQueries({ queryKey: keys.team });
      onCreated(c);
    },
    onError: (e) => {
      if (!isApiError(e) || !Object.keys(e.errors).length) toast(errorMessage(e), "error");
    },
  });
}

/** The accept link, shown once. Sharing is by hand (no email delivery). */
export function InviteLink({ url, email }: { url: string; email: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="rounded-2xl border border-accent/30 bg-accent/5 p-4">
      <p className="text-[13px] font-semibold">Invitation link for {email}</p>
      <p className="mt-1 text-xs text-foreground/55">Copy it now and send it yourself. It won’t be shown again and works once, for 7 days.</p>
      <div className="mt-3 flex gap-2">
        <Input readOnly value={url} aria-label="Invitation link" className="h-10 text-xs" onFocus={(e) => e.target.select()} />
        <Button onClick={() => void copy()} variant="accent" className="shrink-0">
          {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

export function InvitePanel({ employee }: { employee: Employee }) {
  const { role } = useSession();
  const toast = useToast();
  const qc = useQueryClient();
  const scope = useLocationScope();
  const invitations = useInvitations();
  const [created, setCreated] = useState<InvitationCreated | null>(null);
  const [inviteRole, setInviteRole] = useState<"employee" | "manager">("employee");
  const [email, setEmail] = useState(employee.email ?? "");
  const [error, setError] = useState<string | undefined>();
  const invite = useInvite(setCreated);
  const revoke = useMutation({
    mutationFn: (id: string) => api<Invitation>(`/v1/team/invitations/${id}/revoke`, { method: "POST" }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.team });
      toast("Invitation revoked.", "success");
    },
    onError: (e) => toast(errorMessage(e), "error"),
  });

  if (employee.login === "active") {
    return <p className="rounded-2xl bg-muted px-4 py-3 text-[13px] text-foreground/65">{employee.display_name} can log in.</p>;
  }
  if (!employee.active) return null;
  const pending = (invitations.data ?? []).find((i) => i.employee_id === employee.id);

  return (
    <section aria-label="Login" className="space-y-3 rounded-2xl border border-border p-4">
      <p className="text-[13px] font-semibold">Login</p>
      {created ? (
        <InviteLink url={created.accept_url} email={created.invitation.email} />
      ) : pending ? (
        <div className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
          <span className="text-foreground/65">
            Invited {pending.email} · expires {formatDate(pending.expires_at.slice(0, 10))}
          </span>
          <Button variant="ghost" size="sm" loading={revoke.isPending} onClick={() => revoke.mutate(pending.id)}>
            Revoke
          </Button>
        </div>
      ) : (
        <p className="text-xs text-foreground/55">Invite {employee.display_name} to see their schedule and send requests from their phone.</p>
      )}
      {!created && (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (!email.trim()) return setError("Add an email address first");
            setError(undefined);
            invite.mutate(
              { employee_id: employee.id, email: email.trim(), role: inviteRole, location_ids: employee.location_ids },
              { onError: (err) => isApiError(err) && setError(err.errors.email ?? err.errors.location_ids) },
            );
          }}
        >
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_140px]">
            <Field label="Email" error={error}>
              {(p) => <Input {...p} type="email" value={email} onChange={(e) => setEmail(e.target.value)} className="h-10" />}
            </Field>
            <Field label="Role">
              {(p) => (
                <Select
                  {...p}
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value as "employee" | "manager")}
                  className="h-10"
                  disabled={!canManage(role)}
                >
                  <option value="employee">Employee</option>
                  <option value="manager">Manager</option>
                </Select>
              )}
            </Field>
          </div>
          <Button type="submit" variant="outline" size="sm" loading={invite.isPending} disabled={!scope.locations.length}>
            <Send aria-hidden /> {pending ? "Send a new link" : "Create invitation link"}
          </Button>
        </form>
      )}
    </section>
  );
}

/** Owners and admins invite managers who aren't on the schedule (no employee record). */
export function InviteManagerDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="Invite a manager" description="Managers build schedules and approve requests for the locations you choose.">
      {open && <InviteManagerForm onDone={onClose} />}
    </Dialog>
  );
}

function InviteManagerForm({ onDone }: { onDone: () => void }) {
  const scope = useLocationScope();
  const [created, setCreated] = useState<InvitationCreated | null>(null);
  const [email, setEmail] = useState("");
  const [locs, setLocs] = useState(scope.current ? [scope.current.id] : []);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const invite = useInvite(setCreated);

  if (created) {
    return (
      <div className="space-y-4">
        <InviteLink url={created.accept_url} email={created.invitation.email} />
        <div className="flex justify-end">
          <Button onClick={onDone}>Done</Button>
        </div>
      </div>
    );
  }
  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const errs: Record<string, string> = {};
        if (!email.trim()) errs.email = "Enter an email";
        if (!locs.length) errs.location_ids = "Choose at least one location";
        setErrors(errs);
        if (Object.keys(errs).length) return;
        invite.mutate(
          { email: email.trim(), role: "manager", location_ids: locs },
          { onError: (err) => isApiError(err) && setErrors(err.errors) },
        );
      }}
    >
      <Field label="Email" error={errors.email}>
        {(p) => <Input {...p} type="email" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />}
      </Field>
      <LocationsPicker locations={scope.locations} value={locs} onChange={setLocs} error={errors.location_ids} />
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" loading={invite.isPending}>
          Create invitation link
        </Button>
      </div>
    </form>
  );
}
