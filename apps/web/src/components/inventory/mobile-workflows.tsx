"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/field";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import { useLocationScope } from "@/lib/location-scope";

type Area = { id: string; name: string };
type CountLine = { item_id: string; item_name: string; base_unit: string; expected_base_quantity: string; counted_base_quantity: string | null; exception_state: string };
type Session = { id: string; status: string; lines: CountLine[] };
type PurchaseOrder = { id: string; status: string; supplier_name: string };
type PurchaseLine = { id: string; item_name: string; base_unit: string; ordered_quantity: string; received_quantity: string; closed_quantity: string };

export function CountWorkflow() {
  const scope = useLocationScope();
  const searchParams = useSearchParams();
  const cache = useQueryClient();
  const [area, setArea] = useState("");
  const [sessionId, setSessionId] = useState("");
  const activeSessionId = sessionId || searchParams.get("session") || "";
  const [values, setValues] = useState<Record<string, string>>({});
  const [search, setSearch] = useState("");
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({});
  const areas = useQuery({ queryKey: ["inventory-areas", scope.current?.id], queryFn: () => api<{data: Area[]}>("/v1/inventory/storage-areas", { query: { location_id: scope.current?.id } }), enabled: !!scope.current });
  const session = useQuery({ queryKey: ["count-session", activeSessionId], queryFn: () => api<Session>(`/v1/inventory/count-sessions/${activeSessionId}`), enabled: !!activeSessionId });
  const start = useMutation({ mutationFn: () => api<Session>("/v1/inventory/count-sessions", { method: "POST", body: { location_id: scope.current?.id, storage_area_id: area } }), onSuccess: (result) => setSessionId(result.id) });
  const save = useMutation({ mutationFn: ({ item, quantity }: { item: string; quantity: string }) => api(`/v1/inventory/count-sessions/${activeSessionId}/lines/${item}`, { method: "PUT", body: { quantity, unit: session.data?.lines.find((line) => line.item_id === item)?.base_unit ?? "" } }), onSuccess: () => cache.invalidateQueries({ queryKey: ["count-session", activeSessionId] }) });
  const submit = useMutation({ mutationFn: () => api(`/v1/inventory/count-sessions/${activeSessionId}/submit`, { method: "POST", body: {} }), onSuccess: () => cache.invalidateQueries({ queryKey: ["count-session", activeSessionId] }) });
  const confirm = useMutation({ mutationFn: ({ item, note }: { item: string; note: string }) => api(`/v1/inventory/count-sessions/${activeSessionId}/lines/${item}/confirm`, { method: "POST", body: { note } }), onSuccess: () => cache.invalidateQueries({ queryKey: ["count-session", activeSessionId] }) });
  return <div className="space-y-5 pb-20">
    <PageHeader overline="Inventory · phone workflow" title="Kitchen count" description="Enter and save each item. Stock changes only after a manager submits the count." />
    <Link className="text-sm text-accent underline" href={scope.href("/app/inventory")}>Back to inventory</Link>
    {!sessionId && <Card><CardHeader overline="Step 1" title="Choose a storage area" /><div className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto]"><Select aria-label="Storage area" value={area} onChange={(event) => setArea(event.target.value)}><option value="">Choose area</option>{(areas.data?.data ?? []).map((entry) => <option key={entry.id} value={entry.id}>{entry.name}</option>)}</Select><Button className="min-h-12" disabled={!area || start.isPending} loading={start.isPending} onClick={() => start.mutate()}>Start count</Button></div>{areas.isError && <ErrorState error={areas.error} onRetry={() => void areas.refetch()} retrying={areas.isFetching} />}</Card>}
    {activeSessionId && <Card><CardHeader overline={`Step 2 · ${session.data?.status ?? "Loading"}`} title="Count each item" />{session.isPending ? <p className="mt-4 text-sm text-foreground/55">Loading count…</p> : session.isError ? <ErrorState error={session.error} onRetry={() => void session.refetch()} retrying={session.isFetching} /> : session.data?.lines.length === 0 ? <EmptyState title="No items assigned" description="Assign items to this storage area in catalog setup." /> : <div className="mt-4 space-y-3"><Input aria-label="Search count items" placeholder="Search items" value={search} onChange={(event) => setSearch(event.target.value)} />{session.data?.lines.filter((line) => line.item_name.toLowerCase().includes(search.toLowerCase())).map((line) => <form key={line.item_id} className="rounded-xl border border-border p-4" onSubmit={(event) => { event.preventDefault(); save.mutate({ item: line.item_id, quantity: values[line.item_id] ?? line.counted_base_quantity ?? "" }); }}><div className="mb-3 flex items-start justify-between gap-3"><div><p className="font-semibold">{line.item_name}</p><p className="text-xs text-foreground/55">Expected {line.expected_base_quantity} {line.base_unit}</p></div><span className="text-xs text-foreground/50">{line.exception_state.replaceAll("_", " ")}</span></div><div className="flex gap-2"><Input inputMode="decimal" aria-label={`Count ${line.item_name}`} placeholder="Count" className="min-h-12 text-lg" value={values[line.item_id] ?? line.counted_base_quantity ?? ""} onChange={(event) => setValues({ ...values, [line.item_id]: event.target.value })} /><Button className="min-h-12" type="submit" loading={save.isPending}>Save</Button></div>{line.exception_state === "review_required" && <div className="mt-3 flex gap-2"><Input aria-label={`Review note for ${line.item_name}`} placeholder="Reason for confirming this variance" value={reviewNotes[line.item_id] ?? ""} onChange={(event) => setReviewNotes({ ...reviewNotes, [line.item_id]: event.target.value })} /><Button className="min-h-12" type="button" disabled={!reviewNotes[line.item_id]?.trim()} onClick={() => confirm.mutate({ item: line.item_id, note: reviewNotes[line.item_id] ?? "" })}>Confirm</Button></div>}</form>)}<Button className="min-h-12 w-full" disabled={session.data?.status !== "draft" || submit.isPending} loading={submit.isPending} onClick={() => submit.mutate()}>Submit count</Button>{submit.isError && <p role="alert" className="text-sm text-destructive">{submit.error instanceof Error ? submit.error.message : "Could not submit. Refresh and retry."}</p>}</div>}</Card>}
  </div>;
}

export function ReceiveWorkflow() {
  const scope = useLocationScope();
  const searchParams = useSearchParams();
  const [poId, setPoId] = useState("");
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [costs, setCosts] = useState<Record<string, string>>({});
  const [acceptOverage, setAcceptOverage] = useState(false);
  const [overageReason, setOverageReason] = useState("");
  const activeOrderId = poId || searchParams.get("po") || "";
  const orders = useQuery({ queryKey: ["purchase-orders", scope.current?.id], queryFn: () => api<{data: PurchaseOrder[]}>("/v1/inventory/purchase-orders", { query: { location_id: scope.current?.id } }), enabled: !!scope.current });
  const order = useQuery({ queryKey: ["purchase-order", activeOrderId], queryFn: () => api<PurchaseOrder & {lines: PurchaseLine[]}>(`/v1/inventory/purchase-orders/${activeOrderId}`), enabled: !!activeOrderId });
  const receive = useMutation({ mutationFn: () => api("/v1/inventory/purchase-orders/" + activeOrderId + "/receive", { method: "POST", body: { idempotency_key: crypto.randomUUID(), lines: (order.data?.lines ?? []).filter((line) => Number(quantities[line.id] ?? 0) > 0).map((line) => ({ line_id: line.id, received_base_quantity: quantities[line.id], total_cost_minor: Number(costs[line.id] ?? 0), accept_overage: acceptOverage, reason: overageReason })) } }), onSuccess: () => { void orders.refetch(); void order.refetch(); setQuantities({}); setCosts({}); setAcceptOverage(false); setOverageReason(""); } });
  return <div className="space-y-5 pb-20">
    <PageHeader overline="Inventory · phone workflow" title="Receive delivery" description="Record actual received quantities and line costs. A saved receipt updates stock once." />
    <Link className="text-sm text-accent underline" href={scope.href("/app/inventory")}>Back to inventory</Link>
    <Card><CardHeader overline="Step 1" title="Select an open purchase order" /><div className="mt-4"><Select aria-label="Purchase order" value={poId} onChange={(event) => setPoId(event.target.value)}><option value="">Choose order</option>{(orders.data?.data ?? []).filter((entry) => entry.status !== "closed").map((entry) => <option key={entry.id} value={entry.id}>{entry.supplier_name} · {entry.status}</option>)}</Select></div>{orders.isError && <ErrorState error={orders.error} onRetry={() => void orders.refetch()} retrying={orders.isFetching} />}</Card>
    {activeOrderId && <Card><CardHeader overline="Step 2" title="Enter what arrived" />{order.isPending ? <p className="mt-4 text-sm text-foreground/55">Loading order…</p> : order.isError ? <ErrorState error={order.error} onRetry={() => void order.refetch()} retrying={order.isFetching} /> : <div className="mt-4 space-y-3">{order.data?.lines.map((line) => <div key={line.id} className="rounded-xl border border-border p-4"><p className="mb-1 font-semibold">{line.item_name}</p><p className="mb-3 text-xs text-foreground/55">Remaining {Number(line.ordered_quantity) - Number(line.received_quantity) - Number(line.closed_quantity)} {line.base_unit}</p><div className="grid grid-cols-2 gap-2"><Input inputMode="decimal" className="min-h-12 text-lg" aria-label={`Received quantity for ${line.item_name}`} placeholder={line.base_unit} value={quantities[line.id] ?? ""} onChange={(event) => setQuantities({ ...quantities, [line.id]: event.target.value })} /><Input inputMode="numeric" className="min-h-12 text-lg" aria-label={`Total cost for ${line.item_name}`} placeholder="Line cost ¢" value={costs[line.id] ?? ""} onChange={(event) => setCosts({ ...costs, [line.id]: event.target.value })} /></div></div>)}<label className="flex min-h-12 items-center gap-3 text-sm"><input type="checkbox" checked={acceptOverage} onChange={(event) => setAcceptOverage(event.target.checked)} />Manager accepts any delivery overage</label>{acceptOverage && <Input aria-label="Overage reason" placeholder="Reason required for accepting overage" value={overageReason} onChange={(event) => setOverageReason(event.target.value)} />}<Button className="min-h-12 w-full" loading={receive.isPending} disabled={!Object.values(quantities).some((quantity) => Number(quantity) > 0) || (acceptOverage && !overageReason.trim())} onClick={() => receive.mutate()}>Post receipt</Button>{receive.isError && <p role="alert" className="text-sm text-destructive">{receive.error instanceof Error ? receive.error.message : "Could not post receipt. Refresh and retry."}</p>}</div>}</Card>}
  </div>;
}
