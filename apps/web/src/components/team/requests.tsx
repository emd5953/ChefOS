"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Check, Inbox, X } from "lucide-react";
import { useState } from "react";
import { describeBlock, StatusBadge, timeOffDates } from "@/components/my/requests";
import { PageHeader } from "@/components/page-header";
import { PosGate } from "@/components/pos-gate";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage } from "@/lib/api";
import { formatDate, formatDateTime, formatShiftRange, formatWeekday } from "@/lib/format";
import { keys, useRequestsInbox } from "@/lib/queries";
import type { AvailabilitySet, Location, ShiftCoverageRequest, ShiftTrade, TimeOff } from "@/lib/types";
import { Tabs } from "./shared";

export function RequestsInbox() {
  const [tab, setTab] = useState<"pending" | "history">("pending");
  return (
    <>
      <PageHeader
        overline="Team"
        title="Requests"
        description="Time off and availability from your team. Approving time off flags overlapping shifts in the builder; it never deletes them."
      />
      <div className="mb-5">
        <Tabs
          label="Requests"
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "pending", label: "Pending" },
            { id: "history", label: "History" },
          ]}
        />
      </div>
      <PosGate requirePos={false}>{(loc) => <InboxList location={loc} status={tab} />}</PosGate>
    </>
  );
}

function InboxList({ location, status }: { location: Location; status: "pending" | "history" }) {
  const q = useRequestsInbox(location.id, status);
  const coverage = useQuery({
    queryKey: ["team", "shift-coverage-requests", location.id],
    queryFn: async () => (await api<{ data: ShiftCoverageRequest[] }>("/v1/team/shift-coverage-requests", { query: { location_id: location.id } })).data,
    enabled: status === "pending",
  });
  const trades = useQuery({
    queryKey: ["team", "shift-trades", location.id],
    queryFn: async () => (await api<{ data: ShiftTrade[] }>("/v1/team/shift-trades", { query: { location_id: location.id } })).data,
    enabled: status === "pending",
  });
  if (q.isPending) {
    return (
      <div className="space-y-3" role="status" aria-label="Loading requests">
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-28 rounded-card" />
        ))}
      </div>
    );
  }
  if (q.isError) {
    return (
      <Card>
        <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </Card>
    );
  }
  const { time_off, availability } = q.data;
  if (
    !time_off.length &&
    !availability.length &&
    status === "pending" &&
    (coverage.isPending || trades.isPending)
  ) {
    return <Skeleton className="h-28 rounded-card" role="status" aria-label="Loading shift requests" />;
  }
  if (!time_off.length && !availability.length && !coverage.data?.length && !trades.data?.length) {
    return (
      <Card>
        <EmptyState
          icon={<Inbox />}
          title={status === "pending" ? "Nothing to review" : "No decisions yet"}
          description={status === "pending" ? `No pending requests at ${location.name}.` : "Approved and denied requests show up here."}
        />
      </Card>
    );
  }
  return (
    <div className="space-y-6">
      {status === "pending" && <CoverageRequests rows={coverage.data ?? []} location={location} />}
      {status === "pending" && coverage.isError && <Card><ErrorState error={coverage.error} onRetry={() => void coverage.refetch()} retrying={coverage.isFetching} /></Card>}
      {status === "pending" && <ManagerTradeRequests rows={trades.data ?? []} location={location} />}
      {status === "pending" && trades.isError && <Card><ErrorState error={trades.error} onRetry={() => void trades.refetch()} retrying={trades.isFetching} /></Card>}
      <div className="grid gap-6 lg:grid-cols-2">
      <section aria-labelledby="inbox-time-off" className="space-y-3">
        <h2 id="inbox-time-off" className="eyebrow">
          Time off ({time_off.length})
        </h2>
        {time_off.map((t) => (
          <TimeOffCard key={t.id} t={t} tz={location.timezone} pending={status === "pending"} />
        ))}
      </section>
      <section aria-labelledby="inbox-availability" className="space-y-3">
        <h2 id="inbox-availability" className="eyebrow">
          Availability ({availability.length})
        </h2>
        {availability.map((a) => (
          <AvailabilityCard key={a.id} a={a} pending={status === "pending"} />
        ))}
      </section>
      </div>
    </div>
  );
}

function CoverageRequests({ rows, location }: { rows: ShiftCoverageRequest[]; location: Location }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [note, setNote] = useState("");
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "deny" }) =>
      api(`/v1/team/shift-coverage-requests/${id}/decide`, { method: "POST", body: { decision, note: note.trim() || undefined } }),
    onSuccess: (_, vars) => {
      toast(vars.decision === "approve" ? "Request approved. Review and publish the schedule to notify the employee." : "Request declined.", "success");
      setNote("");
      void qc.invalidateQueries({ queryKey: ["team", "shift-coverage-requests", location.id] });
      void qc.invalidateQueries({ queryKey: ["team", "schedule"] });
    },
  });
  if (!rows.length) return null;
  return (
    <section aria-labelledby="coverage-requests" className="space-y-3">
      <h2 id="coverage-requests" className="eyebrow">Open shift requests ({rows.length})</h2>
      {rows.map((r) => (
        <Card key={r.id} className="p-4">
          <div className="mb-3 max-w-sm">
            <Input aria-label="Decision note (optional)" placeholder="Decision note (optional)" value={note} onChange={(event) => setNote(event.target.value)} />
          </div>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="font-semibold">{r.employee_name} · {r.position_name}</p>
              <p className="mt-1 text-sm text-foreground/60">{formatWeekday(r.business_date)}, {formatDate(r.business_date)} · {formatShiftRange(r.starts_at, r.ends_at, location.timezone)}</p>
              <p className="mt-1 text-xs text-foreground/45">Approval updates the draft schedule. Publish it to make the shift visible.</p>
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" loading={decide.isPending} onClick={() => decide.mutate({ id: r.id, decision: "deny" })}>Decline</Button>
              <Button size="sm" loading={decide.isPending} onClick={() => decide.mutate({ id: r.id, decision: "approve" })}>Approve</Button>
            </div>
          </div>
        </Card>
      ))}
      {decide.isError && <p role="alert" className="text-sm text-destructive">{decide.error instanceof Error ? decide.error.message : "Could not decide this request."}</p>}
    </section>
  );
}

function ManagerTradeRequests({ rows, location }: { rows: ShiftTrade[]; location: Location }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [note, setNote] = useState("");
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "deny" }) =>
      api(`/v1/team/shift-trades/${id}/decide`, {
        method: "POST",
        body: { decision, note: note.trim() || undefined },
      }),
    onSuccess: (_, vars) => {
      toast(
        vars.decision === "approve"
          ? "Trade approved. Review and publish the schedule to apply the swap."
          : "Trade declined.",
        "success",
      );
      setNote("");
      void qc.invalidateQueries({ queryKey: ["team", "shift-trades", location.id] });
      void qc.invalidateQueries({ queryKey: ["team", "schedule"] });
    },
  });
  if (!rows.length) return null;
  return (
    <section aria-labelledby="manager-shift-trades" className="space-y-3">
      <h2 id="manager-shift-trades" className="eyebrow">
        Accepted shift trades ({rows.length})
      </h2>
      {rows.map((trade) => (
        <Card key={trade.id} className="p-4">
          <p className="font-semibold">
            {trade.requester_name} ↔ {trade.recipient_name}
          </p>
          <p className="mt-1 text-sm text-foreground/60">
            {formatWeekday(trade.offered_shift.business_date)} {formatDate(trade.offered_shift.business_date)} · {trade.requester_name}: {formatShiftRange(trade.offered_shift.starts_at, trade.offered_shift.ends_at, location.timezone)} ({trade.offered_shift.position_name})
          </p>
          <p className="text-sm text-foreground/60">
            {formatWeekday(trade.requested_shift.business_date)} {formatDate(trade.requested_shift.business_date)} · {trade.recipient_name}: {formatShiftRange(trade.requested_shift.starts_at, trade.requested_shift.ends_at, location.timezone)} ({trade.requested_shift.position_name})
          </p>
          <p className="mt-1 text-xs text-foreground/45">
            Both employees accepted. Approval updates the draft; publish the schedule to apply the swap.
          </p>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <div className="max-w-sm flex-1">
              <Input
                aria-label="Trade decision note (optional)"
                placeholder="Decision note (optional)"
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" loading={decide.isPending} onClick={() => decide.mutate({ id: trade.id, decision: "deny" })}>
                Decline
              </Button>
              <Button size="sm" loading={decide.isPending} onClick={() => decide.mutate({ id: trade.id, decision: "approve" })}>
                Approve
              </Button>
            </div>
          </div>
        </Card>
      ))}
      {decide.isError && <p role="alert" className="text-sm text-destructive">{decide.error instanceof Error ? decide.error.message : "Could not decide this trade."}</p>}
    </section>
  );
}

function useDecide(path: string, label: string) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (body: { decision: string; note?: string }) => api<unknown>(path, { method: "POST", body }),
    onSuccess: async (_, v) => {
      await qc.invalidateQueries({ queryKey: keys.team });
      toast(`${label} ${v.decision === "approve" ? "approved" : v.decision === "deny" ? "denied" : "rejected"}.`, "success");
    },
    onError: (e) => toast(errorMessage(e), "error"),
  });
}

function DecisionBar({ onDecide, loading, negative }: { onDecide: (d: string, note: string) => void; loading: boolean; negative: "deny" | "reject" }) {
  const [note, setNote] = useState("");
  return (
    <div className="mt-4 flex flex-wrap items-center gap-2">
      <Input aria-label="Note to the employee (optional)" placeholder="Note (optional)" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} className="h-9 min-w-0 flex-1" />
      <Button size="sm" variant="outline" onClick={() => onDecide(negative, note)} disabled={loading}>
        <X aria-hidden /> {negative === "deny" ? "Deny" : "Reject"}
      </Button>
      <Button size="sm" onClick={() => onDecide("approve", note)} loading={loading}>
        <Check aria-hidden /> Approve
      </Button>
    </div>
  );
}

function TimeOffCard({ t, tz, pending }: { t: TimeOff; tz: string; pending: boolean }) {
  const decide = useDecide(`/v1/team/time-off/${t.id}/decide`, "Time off");
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[15px] font-semibold tracking-[-0.02em]">{t.employee_name ?? "Employee"}</p>
          <p className="mt-0.5 text-[13px] text-foreground/70">{timeOffDates(t)}</p>
          {t.reason && <p className="mt-1 text-xs text-foreground/55">“{t.reason}”</p>}
        </div>
        <StatusBadge status={t.status} />
      </div>
      {t.overlapping_shifts.length > 0 && (
        <div className="mt-3 rounded-xl bg-warning/10 px-3 py-2 text-[12px] text-warning">
          <p className="flex items-center gap-1.5 font-semibold">
            <CalendarClock className="size-3.5" aria-hidden /> Overlaps {t.overlapping_shifts.length} published shift
            {t.overlapping_shifts.length === 1 ? "" : "s"}
          </p>
          <ul className="mt-1 space-y-0.5">
            {t.overlapping_shifts.map((s) => (
              <li key={s.id}>
                {formatWeekday(s.business_date)} {formatDate(s.business_date)}, {formatShiftRange(s.starts_at, s.ends_at, tz)} · {s.position_name}
              </li>
            ))}
          </ul>
        </div>
      )}
      {pending ? (
        <DecisionBar negative="deny" loading={decide.isPending} onDecide={(d, note) => decide.mutate({ decision: d, note: note || undefined })} />
      ) : (
        <p className="mt-3 text-xs text-foreground/50">
          {t.decided_at ? `Decided ${formatDateTime(t.decided_at, tz)}` : `Requested ${formatDateTime(t.created_at, tz)}`}
          {t.decision_note ? ` · ${t.decision_note}` : ""}
        </p>
      )}
    </Card>
  );
}

function AvailabilityCard({ a, pending }: { a: AvailabilitySet; pending: boolean }) {
  const decide = useDecide(`/v1/team/availability/${a.id}/decide`, "Availability");
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[15px] font-semibold tracking-[-0.02em]">{a.employee_name ?? "Employee"}</p>
          <p className="mt-0.5 text-[13px] text-foreground/70">Starting {formatDate(a.effective_from)}</p>
          {a.note && <p className="mt-1 text-xs text-foreground/55">“{a.note}”</p>}
        </div>
        <StatusBadge status={a.status} />
      </div>
      <p className="mt-3 text-[12px] text-foreground/60">
        {a.blocks.length ? `Unavailable: ${a.blocks.map(describeBlock).join(", ")}` : "Available any time"}
      </p>
      {pending ? (
        <DecisionBar negative="reject" loading={decide.isPending} onDecide={(d, note) => decide.mutate({ decision: d, note: note || undefined })} />
      ) : (
        a.decision_note && <p className="mt-3 text-xs text-foreground/50">{a.decision_note}</p>
      )}
    </Card>
  );
}
