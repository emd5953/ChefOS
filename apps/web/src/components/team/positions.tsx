"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, Briefcase, Plus } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/field";
import { TableSkeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { api, errorMessage, isApiError } from "@/lib/api";
import { keys, usePositions, useStations } from "@/lib/queries";
import type { Location, Position, Station } from "@/lib/types";
import { Checkbox } from "./shared";

export function Positions() {
  const qc = useQueryClient();
  const toast = useToast();
  const [archived, setArchived] = useState(false);
  const q = usePositions(archived);
  const [name, setName] = useState("");
  const [color, setColor] = useState("#2861f5");
  const [error, setError] = useState<string | undefined>();

  const create = useMutation({
    mutationFn: () => api<Position>("/v1/team/positions", { method: "POST", body: { name, color } }),
    onSuccess: async (p) => {
      await qc.invalidateQueries({ queryKey: keys.team });
      toast(`${p.name} added.`, "success");
      setName("");
      setError(undefined);
    },
    onError: (e) => setError(isApiError(e) ? (e.errors.name ?? errorMessage(e)) : errorMessage(e)),
  });

  const rows = q.data ?? [];

  return (
    <Card>
      <form
        className="flex flex-wrap items-end gap-3 p-5 sm:p-6"
        onSubmit={(e) => {
          e.preventDefault();
          if (!name.trim()) return setError("Enter a name");
          create.mutate();
        }}
      >
        <Field label="New position" error={error} className="min-w-[200px] flex-1">
          {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} placeholder="Expo" />}
        </Field>
        <label className="space-y-1.5">
          <span className="block text-[12px] font-semibold text-foreground/80">Color</span>
          <input
            type="color"
            value={color}
            onChange={(e) => setColor(e.target.value)}
            className="h-11 w-14 cursor-pointer rounded-xl border border-input bg-background p-1"
            aria-label="Position color"
          />
        </label>
        <Button type="submit" loading={create.isPending} className="mb-px h-11">
          <Plus aria-hidden /> Add
        </Button>
        <div className="basis-full">
          <Checkbox label="Show archived" checked={archived} onChange={setArchived} />
        </div>
      </form>
      {q.isPending ? (
        <TableSkeleton rows={5} cols={3} />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<Briefcase />}
          title="No positions yet"
          description="Positions are created from Square job titles when your team syncs, or add them here."
        />
      ) : (
        <ul className="divide-y divide-border border-t border-border">
          {rows.map((p) => (
            <PositionRow key={p.id} position={p} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function PositionRow({ position }: { position: Position }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(position.name);
  const patch = useMutation({
    mutationFn: (body: Partial<{ name: string; color: string; archived: boolean }>) =>
      api<Position>(`/v1/team/positions/${position.id}`, { method: "PATCH", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.team }),
    onError: (e) => {
      setName(position.name);
      toast(errorMessage(e), "error");
    },
  });
  const archived = position.archived_at != null;
  return (
    <li className="flex items-center gap-3 px-5 py-2.5 sm:px-6">
      <input
        type="color"
        defaultValue={position.color}
        onBlur={(e) => e.target.value !== position.color && patch.mutate({ color: e.target.value })}
        className="size-8 shrink-0 cursor-pointer rounded-lg border border-border bg-background p-0.5"
        aria-label={`${position.name} color`}
      />
      <Input
        aria-label="Position name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={() => name.trim() && name !== position.name && patch.mutate({ name })}
        className="h-9 max-w-xs border-transparent hover:border-input"
      />
      <span className="flex-1" />
      <Button variant="ghost" size="sm" onClick={() => patch.mutate({ archived: !archived })} loading={patch.isPending}>
        {archived ? <ArchiveRestore aria-hidden /> : <Archive aria-hidden />}
        {archived ? "Restore" : "Archive"}
        <span className="sr-only"> {position.name}</span>
      </Button>
    </li>
  );
}

/** Stations: optional labels on a shift (Grill, Patio), per location. */
export function Stations({ location }: { location: Location }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useStations(location.id);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | undefined>();
  const create = useMutation({
    mutationFn: () => api<Station>("/v1/team/stations", { method: "POST", body: { location_id: location.id, name } }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: keys.team });
      setName("");
      setError(undefined);
    },
    onError: (e) => setError(errorMessage(e)),
  });
  const archive = useMutation({
    mutationFn: (id: string) => api<Station>(`/v1/team/stations/${id}`, { method: "PATCH", body: { archived: true } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.team }),
    onError: (e) => toast(errorMessage(e), "error"),
  });
  return (
    <Card className="mt-5">
      <form
        className="flex flex-wrap items-end gap-3 p-5 sm:p-6"
        onSubmit={(e) => {
          e.preventDefault();
          if (!name.trim()) return setError("Enter a name");
          create.mutate();
        }}
      >
        <Field label={`New station at ${location.name}`} error={error} className="min-w-[200px] flex-1">
          {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} placeholder="Grill" />}
        </Field>
        <Button type="submit" loading={create.isPending} className="mb-px h-11">
          <Plus aria-hidden /> Add
        </Button>
      </form>
      {q.isPending ? (
        <TableSkeleton rows={2} cols={2} />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (q.data ?? []).length === 0 ? (
        <p className="border-t border-border px-5 py-4 text-[13px] text-foreground/50 sm:px-6">No stations. They’re optional.</p>
      ) : (
        <ul className="divide-y divide-border border-t border-border">
          {(q.data ?? []).map((s) => (
            <li key={s.id} className="flex items-center justify-between px-5 py-2.5 text-[13px] sm:px-6">
              {s.name}
              <Button variant="ghost" size="sm" onClick={() => archive.mutate(s.id)}>
                <Archive aria-hidden /> Archive<span className="sr-only"> {s.name}</span>
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
