"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/field";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";

type Item = { id: string; name: string; base_unit: string; active: boolean };
type Waste = { id: string; item_name: string; quantity_base: string; base_unit: string; reason: string; business_date: string; created_at: string };

export function WasteLog({ locationId }: { locationId?: string }) {
  const cache = useQueryClient();
  const today = new Date().toLocaleDateString("en-CA");
  const [itemId, setItemId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState("");
  const items = useQuery({ queryKey: ["inventory", locationId], queryFn: () => api<{ data: Item[] }>("/v1/inventory/items", { query: { location_id: locationId } }), enabled: !!locationId });
  const log = useQuery({ queryKey: ["inventory-waste", locationId, today], queryFn: () => api<{ data: Waste[] }>("/v1/inventory/waste", { query: { location_id: locationId, from: today, to: today } }), enabled: !!locationId });
  const save = useMutation({ mutationFn: () => api<Waste>("/v1/inventory/waste", { method: "POST", body: { location_id: locationId, item_id: itemId, quantity: Number(quantity), reason, idempotency_key: crypto.randomUUID() } }), onSuccess: async () => { setQuantity(""); setReason(""); await Promise.all([cache.invalidateQueries({ queryKey: ["inventory", locationId] }), cache.invalidateQueries({ queryKey: ["inventory-waste", locationId] })]); } });
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); save.mutate(); }
  const available = (items.data?.data ?? []).filter((item) => item.active);
  return <Card>
    <CardHeader overline="Inventory adjustment" title="Log waste" />
    <form className="mt-4 grid gap-3 sm:grid-cols-[1.4fr_1fr_1.4fr_auto] sm:items-end" onSubmit={submit}>
      <label className="space-y-1.5 text-xs font-semibold">Item<Select required value={itemId} onChange={(event) => setItemId(event.target.value)}><option value="">Choose item</option>{available.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.base_unit}</option>)}</Select></label>
      <label className="space-y-1.5 text-xs font-semibold">Quantity in base units<Input required type="number" min="0.000001" step="any" value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
      <label className="space-y-1.5 text-xs font-semibold">Reason<Input required value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Spoiled, dropped, over-prepped…" /></label>
      <Button type="submit" disabled={!locationId || !itemId || !quantity || !reason.trim() || save.isPending} loading={save.isPending}>Record waste</Button>
    </form>
    {save.isError && <p className="mt-3 text-sm text-destructive" role="alert">{save.error instanceof Error ? save.error.message : "Could not record waste."}</p>}
    <div className="mt-6 border-t border-border pt-4"><h3 className="text-sm font-semibold">Today&apos;s waste</h3>
      {log.isPending ? <p className="mt-3 text-sm text-foreground/50">Loading waste…</p> : log.isError ? <ErrorState error={log.error} onRetry={() => void log.refetch()} retrying={log.isFetching} /> : !log.data?.data.length ? <EmptyState title="No waste logged today" description="Waste entries reduce stock and are kept in the audit history." /> : <div className="mt-2 divide-y divide-border">{log.data.data.map((entry) => <div className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm" key={entry.id}><span className="font-medium">{entry.item_name} · {entry.quantity_base} {entry.base_unit}</span><span className="text-foreground/60">{entry.reason} · {new Date(entry.created_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</span></div>)}</div>}
    </div>
  </Card>;
}
