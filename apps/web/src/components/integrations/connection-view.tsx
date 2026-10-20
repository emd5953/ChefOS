"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Link2, RefreshCw, Unplug } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Select } from "@/components/ui/field";
import { Skeleton, TableSkeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage } from "@/lib/api";
import { canManage, useSession } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { todayIn } from "@/lib/dates";
import { formatDate, formatDateTime, formatRelative } from "@/lib/format";
import { useLocationScope } from "@/lib/location-scope";
import {
  isBusy,
  keys,
  useConnectionDetail,
  useLocations,
  useMappingCandidates,
  useMappingIssues,
  useResolvedIssues,
} from "@/lib/queries";
import type { Connection, ConnectionDetail, MappingIssue, MappingIssueKind, SyncRun } from "@/lib/types";
import { useNow } from "@/lib/use-now";
import { useStartSquare, SquareMark } from "./integrations";
import { ConnectionPill, RunPill, STREAM_LABEL, StreamPill, backfillOf } from "./status";

export function ConnectionView({ connectionId }: { connectionId: string }) {
  const q = useConnectionDetail(connectionId);

  if (q.isPending) {
    return (
      <div className="space-y-6" role="status" aria-label="Loading connection">
        <Skeleton className="h-36 rounded-card" />
        <Card>
          <TableSkeleton rows={5} cols={5} />
        </Card>
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
  return <ConnectionBody conn={q.data} />;
}

function ConnectionBody({ conn }: { conn: ConnectionDetail }) {
  const busy = isBusy(conn);
  return (
    <div className="space-y-6">
      <ConnectionHeader conn={conn} busy={busy} />
      <StreamsCard conn={conn} />
      <LinksCard conn={conn} />
      <IssuesCard busy={busy} />
      <RunsCard runs={conn.recent_runs} />
    </div>
  );
}

// ---------- Header: status, sync, disconnect ----------

function ConnectionHeader({ conn, busy }: { conn: ConnectionDetail; busy: boolean }) {
  const { role } = useSession();
  const allowed = canManage(role);
  const qc = useQueryClient();
  const toast = useToast();
  const [confirm, setConfirm] = useState(false);
  const reconnect = useStartSquare();

  const sync = useMutation({
    mutationFn: () => api<{ workflow_id: string }>(`/v1/integrations/connections/${conn.id}/sync`, { method: "POST", body: {} }),
    onSuccess: async () => {
      toast("Sync started.", "success");
      await qc.invalidateQueries({ queryKey: keys.connection(conn.id) });
    },
    onError: (e) => toast(errorMessage(e), "error"),
  });

  const disconnect = useMutation({
    mutationFn: () => api<void>(`/v1/integrations/connections/${conn.id}`, { method: "DELETE" }),
    onSuccess: async () => {
      setConfirm(false);
      qc.removeQueries({ queryKey: keys.connection(conn.id) });
      await qc.invalidateQueries({ queryKey: keys.connections });
      toast("Square disconnected.", "success");
    },
    onError: (e) => toast(errorMessage(e), "error"),
  });

  const needsAttention = conn.status === "needs_reauth" || conn.status === "error";

  return (
    <Card>
      <CardBody className="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex gap-5">
          <SquareMark />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-2xl font-semibold tracking-[-0.05em]">Square</h2>
              <ConnectionPill status={conn.status} />
              {conn.mode === "fake" ? (
                <Badge tone="accent">Simulated sandbox</Badge>
              ) : (
                <Badge>Square sandbox</Badge>
              )}
            </div>
            <p className="mt-2 text-[13px] text-foreground/55">
              {conn.external_account_id ? `Account ${conn.external_account_id} · ` : ""}
              Connected {formatDate(conn.created_at.slice(0, 10), true)}
            </p>
            {busy && (
              <p className="mt-2 flex items-center gap-1.5 text-xs font-medium text-accent" role="status">
                <RefreshCw className="size-3 animate-spin" aria-hidden /> Syncing, checking every few seconds
              </p>
            )}
          </div>
        </div>
        {allowed && (
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => sync.mutate()} loading={sync.isPending}>
              {!sync.isPending && <RefreshCw aria-hidden />} Sync now
            </Button>
            <Button variant="outline" onClick={() => setConfirm(true)}>
              <Unplug aria-hidden /> Disconnect
            </Button>
          </div>
        )}
      </CardBody>

      {needsAttention && (
        <div className="mx-6 mb-6 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-destructive/25 bg-destructive/6 px-4 py-3 text-[13px]">
          <p className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="size-4 shrink-0" aria-hidden />
            {conn.status === "needs_reauth"
              ? "Square needs you to reauthorize ChefOS before syncing can continue."
              : "This connection hit an error. Check the stream table below."}
          </p>
          {allowed && conn.status === "needs_reauth" && (
            <Button size="sm" variant="destructive" onClick={() => reconnect.mutate()} loading={reconnect.isPending}>
              Reconnect Square
            </Button>
          )}
        </div>
      )}

      <Dialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Disconnect Square?"
        description="ChefOS will stop importing orders and timecards. Data already imported stays, but it will go stale until you reconnect."
      >
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setConfirm(false)}>
            Cancel
          </Button>
          <Button variant="destructive" loading={disconnect.isPending} onClick={() => disconnect.mutate()}>
            Disconnect
          </Button>
        </div>
      </Dialog>
    </Card>
  );
}

// ---------- Streams ----------

function StreamsCard({ conn }: { conn: Connection }) {
  const now = useNow();
  const { current } = useLocationScope();
  const today = useMemo(() => todayIn(current?.timezone), [current?.timezone]);

  return (
    <Card>
      <CardHeader overline="Data streams" title="Sync health" description="Backfill runs newest to oldest." />
      {conn.streams.length === 0 ? (
        <EmptyState title="No streams yet" description="Streams appear once the first sync starts." />
      ) : (
        <div className="mt-4 overflow-x-auto scrollbar-thin" tabIndex={0} role="region" aria-label="Stream status">
          <table className="w-full min-w-[760px] border-separate border-spacing-0 text-[13px]">
            <thead>
              <tr className="text-left">
                {["Stream", "Status", "Data as of", "Backfill", "Last error"].map((h) => (
                  <th key={h} scope="col" className="eyebrow border-b border-border px-3 py-2 text-[10px] first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {conn.streams.map((s) => {
                const bf = backfillOf(s, today);
                return (
                  <tr key={s.stream} className="align-top">
                    <td className="border-b border-border/70 px-3 py-3.5 pl-5 font-medium sm:pl-6">{STREAM_LABEL[s.stream] ?? s.stream}</td>
                    <td className="border-b border-border/70 px-3 py-3.5">
                      <StreamPill status={s.status} />
                    </td>
                    <td className="border-b border-border/70 px-3 py-3.5 text-foreground/70">
                      {s.data_as_of ? (
                        <time dateTime={s.data_as_of} title={formatDateTime(s.data_as_of)}>
                          {formatRelative(s.data_as_of, now)}
                        </time>
                      ) : (
                        <span className="text-foreground/35">Not yet</span>
                      )}
                    </td>
                    <td className="w-56 border-b border-border/70 px-3 py-3.5">
                      {bf.kind === "none" ? (
                        <span className="text-foreground/35">&mdash;</span>
                      ) : (
                        <div>
                          <div
                            role="progressbar"
                            aria-label={`${STREAM_LABEL[s.stream] ?? s.stream} backfill`}
                            aria-valuemin={0}
                            aria-valuemax={100}
                            aria-valuenow={bf.pct}
                            className="h-1.5 w-full overflow-hidden rounded-full bg-foreground/[0.07]"
                          >
                            <div
                              className={cn(
                                "h-full rounded-full transition-[width] duration-500 ease-out",
                                bf.kind === "done" ? "bg-success" : "bg-accent",
                              )}
                              style={{ width: `${bf.pct}%` }}
                            />
                          </div>
                          <p className="mt-1.5 flex items-center gap-1 text-xs text-foreground/50 tabular">
                            {bf.kind === "done" ? (
                              <>
                                <CheckCircle2 className="size-3 text-success" aria-hidden /> Complete
                                {s.backfill_target_from && ` back to ${formatDate(s.backfill_target_from, true)}`}
                              </>
                            ) : (
                              <>
                                {bf.pct}%{bf.oldestDone ? ` · back to ${formatDate(bf.oldestDone, true)}` : ""}
                              </>
                            )}
                          </p>
                        </div>
                      )}
                    </td>
                    <td className="max-w-xs border-b border-border/70 px-3 py-3.5 pr-5 text-xs text-destructive sm:pr-6">
                      {s.last_error ? <span className="line-clamp-2" title={s.last_error}>{s.last_error}</span> : <span className="text-foreground/30">&mdash;</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// ---------- Location links ----------

function LinksCard({ conn }: { conn: Connection }) {
  const { role } = useSession();
  const allowed = canManage(role);
  const qc = useQueryClient();
  const toast = useToast();
  const locations = useLocations();
  const [draft, setDraft] = useState<Record<string, string | null>>({});

  const valueOf = (extId: string, saved: string | null) => (extId in draft ? (draft[extId] ?? null) : saved);
  const dirty = conn.location_links.some((l) => valueOf(l.external_location_id, l.location_id) !== l.location_id);

  const save = useMutation({
    mutationFn: () =>
      api<Connection>(`/v1/integrations/connections/${conn.id}/locations`, {
        method: "PATCH",
        body: {
          links: conn.location_links.map((l) => ({
            external_location_id: l.external_location_id,
            location_id: valueOf(l.external_location_id, l.location_id),
          })),
        },
      }),
    onSuccess: async () => {
      setDraft({});
      toast("Location links saved.", "success");
      await qc.invalidateQueries({ queryKey: keys.connection(conn.id) });
      await qc.invalidateQueries({ queryKey: keys.issues });
    },
    onError: (e) => toast(errorMessage(e), "error"),
  });

  return (
    <Card>
      <CardHeader
        overline="Mapping"
        title="Location links"
        description="Choose which ChefOS location each Square location reports into."
        action={
          allowed && (
            <Button size="sm" onClick={() => save.mutate()} disabled={!dirty} loading={save.isPending}>
              Save links
            </Button>
          )
        }
      />
      <CardBody>
        {conn.location_links.length === 0 ? (
          <EmptyState icon={<Link2 />} title="No Square locations found" description="They appear after the first locations sync." className="py-6" />
        ) : (
          <ul className="divide-y divide-border">
            {conn.location_links.map((l) => {
              const id = `link-${l.external_location_id}`;
              const value = valueOf(l.external_location_id, l.location_id);
              return (
                <li key={l.external_location_id} className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <label htmlFor={id} className="block truncate text-[14px] font-semibold tracking-[-0.02em]">
                      {l.external_name}
                    </label>
                    <p className="text-xs text-foreground/45">Square &middot; {l.external_location_id}</p>
                  </div>
                  <div className="flex items-center gap-3 sm:w-72">
                    {!value && <Badge tone="warning">Unlinked</Badge>}
                    <Select
                      id={id}
                      className="h-10 text-[13px]"
                      disabled={!allowed || locations.isPending}
                      value={value ?? ""}
                      onChange={(e) => setDraft((d) => ({ ...d, [l.external_location_id]: e.target.value || null }))}
                    >
                      <option value="">Not linked</option>
                      {(locations.data ?? [])
                        .filter((x) => !x.archived_at)
                        .map((x) => (
                          <option key={x.id} value={x.id}>
                            {x.name}
                          </option>
                        ))}
                    </Select>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

// ---------- Mapping issues ----------

const ISSUE_LABEL: Record<MappingIssueKind, string> = {
  unmapped_location: "Unmapped location",
  unmapped_item: "Unmapped item",
  unmapped_team_member: "Unmapped team member",
  unknown_channel: "Unknown channel",
};

// Where the real fix lives, in one line per kind.
const ISSUE_HELP: Record<MappingIssueKind, string> = {
  unmapped_location: "Link this Square location above to import its orders.",
  unmapped_item:
    "Sold as an item that isn’t on your Square menu. Add it to the menu in Square, map it to a menu item here, or ignore it. Sales are counted either way.",
  unmapped_team_member:
    "Clocked in on Square but not in your team directory. Add them in Square or map them to a staff member. Hours count toward labor either way.",
  unknown_channel: "An order source ChefOS doesn’t recognize. Its sales are counted under Other.",
};

const MAP_NOUN: Partial<Record<MappingIssueKind, string>> = {
  unmapped_item: "menu item",
  unmapped_team_member: "staff member",
};

function IssueRow({
  issue,
  now,
  meta,
  actions,
}: {
  issue: MappingIssue;
  now: number;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <li className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 text-[14px] font-semibold tracking-[-0.02em]">
          <span className="truncate">{issue.label}</span>
          <Badge>{ISSUE_LABEL[issue.kind]}</Badge>
          {meta}
        </p>
        <p className="mt-1 text-xs text-foreground/60">{ISSUE_HELP[issue.kind]}</p>
        <p className="mt-1 text-xs text-foreground/50">
          {issue.occurrences.toLocaleString("en-US")} occurrence{issue.occurrences === 1 ? "" : "s"} &middot; last seen{" "}
          {formatRelative(issue.last_seen_at, now)} &middot; <span className="font-mono text-[11px]">{issue.external_id}</span>
        </p>
      </div>
      {actions && <div className="flex shrink-0 gap-2">{actions}</div>}
    </li>
  );
}

function MapDialog({ issue, onClose }: { issue: MappingIssue | null; onClose: () => void }) {
  const candidates = useMappingCandidates(issue?.id);
  const [choice, setChoice] = useState("");
  const qc = useQueryClient();
  const toast = useToast();
  const noun = issue ? MAP_NOUN[issue.kind] ?? "record" : "record";

  const map = useMutation({
    mutationFn: (v: { id: string; entity_id: string }) =>
      api<MappingIssue>(`/v1/integrations/mapping-issues/${v.id}/resolve`, {
        method: "POST",
        body: { entity_id: v.entity_id },
      }),
    onSuccess: async () => {
      toast("Mapped. Past and future records now use it.", "success");
      setChoice("");
      onClose();
      await qc.invalidateQueries({ queryKey: ["mapping-issues"] });
    },
    onError: (e) => toast(errorMessage(e), "error"),
  });

  return (
    <Dialog
      open={issue !== null}
      onOpenChange={(open) => {
        if (!open) {
          setChoice("");
          onClose();
        }
      }}
      title={`Map “${issue?.label ?? ""}”`}
      description={`Choose the ${noun} this is. Existing records are updated, and future syncs match it automatically.`}
    >
      <Field label={noun.charAt(0).toUpperCase() + noun.slice(1)} error={candidates.isError ? errorMessage(candidates.error) : undefined}>
        {(f) => (
          <Select {...f} value={choice} disabled={candidates.isPending} onChange={(e) => setChoice(e.target.value)}>
            <option value="">{candidates.isPending ? "Loading…" : `Select a ${noun}`}</option>
            {(candidates.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button
          disabled={!choice || !issue}
          loading={map.isPending}
          onClick={() => issue && map.mutate({ id: issue.id, entity_id: choice })}
        >
          Map
        </Button>
      </div>
    </Dialog>
  );
}

function IssuesCard({ busy }: { busy: boolean }) {
  const { role } = useSession();
  const allowed = canManage(role);
  const interval = busy ? 3000 : 30_000;
  const q = useMappingIssues(interval);
  const resolved = useResolvedIssues(interval);
  const qc = useQueryClient();
  const toast = useToast();
  const now = useNow();
  const [mapping, setMapping] = useState<MappingIssue | null>(null);

  const ignore = useMutation({
    mutationFn: (id: string) =>
      api<MappingIssue>(`/v1/integrations/mapping-issues/${id}/resolve`, { method: "POST", body: { ignore: true } }),
    onSuccess: async () => {
      toast("Issue ignored.", "success");
      await qc.invalidateQueries({ queryKey: ["mapping-issues"] });
    },
    onError: (e) => toast(errorMessage(e), "error"),
  });

  const mapButton = (i: MappingIssue) =>
    allowed && i.mappable ? (
      <Button variant="outline" size="sm" onClick={() => setMapping(i)}>
        Map<span className="sr-only"> {i.label}</span>
      </Button>
    ) : null;

  const issues = q.data ?? [];
  const recurring = (resolved.data ?? []).filter((i) => i.resolution === "ignored" && i.new_since_resolved > 0);
  return (
    <Card>
      <CardHeader
        overline="Needs attention"
        title="Open mapping issues"
        description="Records ChefOS couldn’t match to something it knows about."
        action={issues.length > 0 ? <Badge tone="warning">{issues.length} open</Badge> : undefined}
      />
      <div className="mt-4">
        {q.isPending ? (
          <TableSkeleton rows={3} cols={3} />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : issues.length === 0 ? (
          <EmptyState icon={<CheckCircle2 />} title="Nothing to fix" description="Every location, item, and channel is mapped." className="py-10" />
        ) : (
          <ul className="divide-y divide-border border-t border-border">
            {issues.map((i) => (
              <IssueRow
                key={i.id}
                issue={i}
                now={now}
                actions={
                  allowed && (
                    <>
                      {mapButton(i)}
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => ignore.mutate(i.id)}
                        loading={ignore.isPending && ignore.variables === i.id}
                      >
                        Ignore<span className="sr-only"> {i.label}</span>
                      </Button>
                    </>
                  )
                }
              />
            ))}
          </ul>
        )}
        {recurring.length > 0 && (
          <div className="border-t border-border">
            <p className="px-5 pt-4 text-xs font-semibold uppercase tracking-[0.06em] text-foreground/50 sm:px-6">
              Ignored, still occurring
            </p>
            <ul className="divide-y divide-border">
              {recurring.map((i) => (
                <IssueRow
                  key={i.id}
                  issue={i}
                  now={now}
                  meta={<Badge tone="warning">+{i.new_since_resolved.toLocaleString("en-US")} since ignored</Badge>}
                  actions={mapButton(i)}
                />
              ))}
            </ul>
          </div>
        )}
      </div>
      <MapDialog issue={mapping} onClose={() => setMapping(null)} />
    </Card>
  );
}

// ---------- Recent runs ----------

function RunsCard({ runs }: { runs: SyncRun[] }) {
  return (
    <Card>
      <CardHeader overline="Activity" title="Recent sync runs" description="Newest 20 runs." />
      {runs.length === 0 ? (
        <EmptyState title="No runs yet" description="Runs show up as soon as a sync starts." className="py-10" />
      ) : (
        <div className="mt-4 overflow-x-auto scrollbar-thin" tabIndex={0} role="region" aria-label="Recent sync runs">
          <table className="w-full min-w-[760px] border-separate border-spacing-0 text-[13px]">
            <thead>
              <tr className="text-left">
                {["Started", "Stream", "Kind", "Status", "Fetched", "Changed", "Duration"].map((h, i) => (
                  <th key={h} scope="col" className={cn("eyebrow border-b border-border px-3 py-2 text-[10px] first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6", i >= 4 && "text-right")}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="group align-top">
                  <td className="border-b border-border/70 px-3 py-3 pl-5 tabular sm:pl-6">{formatDateTime(r.started_at)}</td>
                  <td className="border-b border-border/70 px-3 py-3 font-medium">{STREAM_LABEL[r.stream] ?? r.stream}</td>
                  <td className="border-b border-border/70 px-3 py-3 capitalize text-foreground/60">{r.kind}</td>
                  <td className="border-b border-border/70 px-3 py-3">
                    <RunPill status={r.status} />
                    {r.error_detail && (
                      <p className="mt-1 max-w-56 text-xs text-destructive" title={r.error_detail}>
                        <span className="line-clamp-2">{r.error_detail}</span>
                      </p>
                    )}
                  </td>
                  <td className="border-b border-border/70 px-3 py-3 text-right tabular">{r.fetched_count.toLocaleString("en-US")}</td>
                  <td className="border-b border-border/70 px-3 py-3 text-right tabular">{r.changed_count.toLocaleString("en-US")}</td>
                  <td className="border-b border-border/70 px-3 py-3 pr-5 text-right tabular text-foreground/60 sm:pr-6">
                    {duration(r)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function duration(r: SyncRun): string {
  if (!r.finished_at) return r.status === "running" ? "…" : "—";
  const s = Math.max(0, Math.round((new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}
