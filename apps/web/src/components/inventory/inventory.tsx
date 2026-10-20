"use client";

import { Package } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { Card, CardHeader } from "@/components/ui/card";
import { TableSkeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import { useLocationScope } from "@/lib/location-scope";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Input, Select } from "@/components/ui/field";
import { Button } from "@/components/ui/button";

type StockItem = { id: string; name: string; category: string; base_unit: string; on_hand_base_quantity: number; par_base_quantity: number | null; below_par: boolean };

export function Inventory() {
  const { current } = useLocationScope();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [dimension, setDimension] = useState("mass");
  const [category, setCategory] = useState("");
  const createItem = useMutation({
    mutationFn: () => api("/v1/inventory/items", { method: "POST", body: { name, category, dimension, base_unit: dimension === "mass" ? "g" : dimension === "volume" ? "ml" : "ea" } }),
    onSuccess: async () => { setName(""); setCategory(""); await client.invalidateQueries({ queryKey: ["inventory", current?.id] }); },
  });
  function submitItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (name.trim()) createItem.mutate();
  }
  const query = useQuery({
    queryKey: ["inventory", current?.id],
    queryFn: () => api<{ data: StockItem[] }>("/v1/inventory/items", { query: { location_id: current?.id } }),
    enabled: !!current,
  });
  const items = query.data?.data ?? [];
  return (
    <div className="space-y-6">
      <PageHeader overline={current ? `Inventory · ${current.name}` : "Inventory"} title="Inventory" description="On hand, pars, and the items your kitchen needs to count." />
      <Card>
        <CardHeader overline="Catalog" title="Add an inventory item" />
        <form className="mt-4 grid gap-3 sm:grid-cols-[1.4fr_1fr_1fr_auto] sm:items-end" onSubmit={submitItem}>
          <label className="space-y-1.5 text-xs font-semibold">Item name<Input required value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Roma tomatoes" /></label>
          <label className="space-y-1.5 text-xs font-semibold">Category<Input value={category} onChange={(event) => setCategory(event.target.value)} placeholder="Produce" /></label>
          <label className="space-y-1.5 text-xs font-semibold">Dimension<Select value={dimension} onChange={(event) => setDimension(event.target.value)}><option value="mass">Mass · grams</option><option value="volume">Volume · milliliters</option><option value="count">Count · each</option></Select></label>
          <Button type="submit" disabled={!current || createItem.isPending} loading={createItem.isPending}>Add item</Button>
        </form>
        {createItem.isError && <p className="mt-3 text-xs text-destructive" role="alert">{createItem.error instanceof Error ? createItem.error.message : "Could not save item. Try again."}</p>}
      </Card>
      <Card>
        <CardHeader overline="Stock and pars" title="Inventory items" />
        <div className="mt-4">
          {query.isPending ? <TableSkeleton rows={6} cols={4} /> : query.isError ? <ErrorState error={query.error} onRetry={() => void query.refetch()} retrying={query.isFetching} /> : items.length === 0 ? <EmptyState icon={<Package />} title="No inventory items yet" description="Add your first ingredient and configure its base unit, supplier, and par." /> : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-[13px]"><thead><tr className="eyebrow text-left text-[10px]">{["Item", "Category", "On hand", "Par"].map((x) => <th className="border-b border-border px-3 py-2 first:pl-5" key={x}>{x}</th>)}</tr></thead>
                <tbody>{items.map((item) => <tr key={item.id}><td className="border-b border-border/70 px-3 py-3 pl-5 font-medium">{item.name}</td><td className="border-b border-border/70 px-3 py-3 text-foreground/60">{item.category || "—"}</td><td className={`border-b border-border/70 px-3 py-3 tabular ${item.below_par ? "text-warning" : ""}`}>{item.on_hand_base_quantity} {item.base_unit}</td><td className="border-b border-border/70 px-3 py-3 tabular">{item.par_base_quantity == null ? "—" : `${item.par_base_quantity} ${item.base_unit}`}</td></tr>)}</tbody>
              </table>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
