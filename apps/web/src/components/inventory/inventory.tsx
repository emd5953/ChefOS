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
import Link from "next/link";
import { CountSessionHistory } from "@/components/inventory/management";

type StockItem = { id: string; name: string; category: string; base_unit: string; on_hand_base_quantity: number; par_base_quantity: number | null; below_par: boolean; last_received_cost_minor: number | null; cost_as_of: string | null };

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
      <div className="grid grid-cols-2 gap-3">
        <Link className="flex min-h-12 items-center justify-center rounded-xl bg-foreground px-4 text-sm font-semibold text-background" href={current ? `/app/inventory/count?loc=${current.id}` : "/app/inventory/count"}>Start a count</Link>
        <Link className="flex min-h-12 items-center justify-center rounded-xl border border-border px-4 text-sm font-semibold" href={current ? `/app/inventory/receive?loc=${current.id}` : "/app/inventory/receive"}>Receive delivery</Link>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Link className="rounded-lg border border-border p-3 text-center text-sm font-medium hover:bg-muted" href={current ? `/app/inventory/purchasing?loc=${current.id}` : "/app/inventory/purchasing"}>Purchase orders</Link>
        <Link className="rounded-lg border border-border p-3 text-center text-sm font-medium hover:bg-muted" href={current ? `/app/inventory/recipes?loc=${current.id}` : "/app/inventory/recipes"}>Recipes & costs</Link>
        <Link className="rounded-lg border border-border p-3 text-center text-sm font-medium hover:bg-muted" href={current ? `/app/inventory/count?loc=${current.id}` : "/app/inventory/count"}>Count sessions</Link>
        <Link className="rounded-lg border border-border p-3 text-center text-sm font-medium hover:bg-muted" href={current ? `/app/inventory/receive?loc=${current.id}` : "/app/inventory/receive"}>Receive</Link>
      </div>
      <CatalogSetup locationId={current?.id} />
      <CountSessionHistory />
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
              <table className="w-full min-w-[680px] text-[13px]"><thead><tr className="eyebrow text-left text-[10px]">{["Item", "Category", "On hand", "Par", "Last received cost"].map((x) => <th className="border-b border-border px-3 py-2 first:pl-5" key={x}>{x}</th>)}</tr></thead>
                <tbody>{items.map((item) => <tr key={item.id}><td className="border-b border-border/70 px-3 py-3 pl-5 font-medium">{item.name}</td><td className="border-b border-border/70 px-3 py-3 text-foreground/60">{item.category || "—"}</td><td className={`border-b border-border/70 px-3 py-3 tabular ${item.below_par ? "text-warning" : ""}`}>{item.on_hand_base_quantity} {item.base_unit}</td><td className="border-b border-border/70 px-3 py-3 tabular">{item.par_base_quantity == null ? "—" : `${item.par_base_quantity} ${item.base_unit}`}</td><td className="border-b border-border/70 px-3 py-3 tabular" title={item.cost_as_of ? `As of ${item.cost_as_of}` : undefined}>{item.last_received_cost_minor == null ? "No receipt cost" : `$${(item.last_received_cost_minor / 100).toFixed(4)} / ${item.base_unit}`}</td></tr>)}</tbody>
              </table>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}

type CatalogItem = StockItem & { active: boolean; dimension: string };
type Supplier = { id: string; name: string; active: boolean; contact_name?: string | null; email?: string | null; phone?: string | null };
type StorageArea = { id: string; name: string; active: boolean };
type Pack = { id: string; item_id: string; item_name: string; supplier_id: string; name: string; base_quantity: string; last_price_minor: number | null; active: boolean };

function CatalogSetup({ locationId }: { locationId: string | undefined }) {
  const cache = useQueryClient();
  const [supplierName, setSupplierName] = useState("");
  const [supplierContact, setSupplierContact] = useState("");
  const [supplierEmail, setSupplierEmail] = useState("");
  const [supplierPhone, setSupplierPhone] = useState("");
  const [areaName, setAreaName] = useState("");
  const [packItem, setPackItem] = useState("");
  const [packSupplier, setPackSupplier] = useState("");
  const [packName, setPackName] = useState("");
  const [packUnit, setPackUnit] = useState("case");
  const [packQuantity, setPackQuantity] = useState("");
  const [conversionError, setConversionError] = useState("");
  const suppliers = useQuery({ queryKey: ["inventory-suppliers"], queryFn: () => api<{data: Supplier[]}>("/v1/inventory/suppliers") });
  const packs = useQuery({ queryKey: ["inventory-packs"], queryFn: () => api<{data: Pack[]}>("/v1/inventory/packs") });
  const areas = useQuery({ queryKey: ["inventory-areas", locationId], queryFn: () => api<{data: StorageArea[]}>("/v1/inventory/storage-areas", { query: { location_id: locationId } }), enabled: !!locationId });
  const items = useQuery({ queryKey: ["inventory", locationId], queryFn: () => api<{data: CatalogItem[]}>("/v1/inventory/items", { query: { location_id: locationId } }), enabled: !!locationId });
  const refresh = async () => { await Promise.all([cache.invalidateQueries({ queryKey: ["inventory", locationId] }), cache.invalidateQueries({ queryKey: ["inventory-suppliers"] }), cache.invalidateQueries({ queryKey: ["inventory-areas", locationId] })]); };
  const addSupplier = useMutation({ mutationFn: () => api("/v1/inventory/suppliers", { method: "POST", body: { name: supplierName, contact_name: supplierContact || null, email: supplierEmail || null, phone: supplierPhone || null } }), onSuccess: async () => { setSupplierName(""); setSupplierContact(""); setSupplierEmail(""); setSupplierPhone(""); await refresh(); } });
  const addArea = useMutation({ mutationFn: () => api("/v1/inventory/storage-areas", { method: "POST", body: { location_id: locationId, name: areaName } }), onSuccess: async () => { setAreaName(""); await refresh(); } });
  const addPack = useMutation({ mutationFn: () => api(`/v1/inventory/items/${packItem}/packs`, { method: "POST", body: { item_id: packItem, supplier_id: packSupplier, name: packName, purchase_unit: packUnit, base_quantity: Number(packQuantity) } }), onSuccess: () => { setPackName(""); setPackQuantity(""); } });
  const updateItem = useMutation({ mutationFn: ({ id, active }: { id: string; active: boolean }) => api(`/v1/inventory/items/${id}`, { method: "PATCH", body: { active } }), onSuccess: refresh });
  const saveItem = useMutation({ mutationFn: ({ id, name, category }: { id: string; name: string; category: string }) => api(`/v1/inventory/items/${id}`, { method: "PATCH", body: { name, category } }), onSuccess: refresh });
  const saveConversions = useMutation({ mutationFn: ({ id, conversions }: { id: string; conversions: Record<string, number> }) => api(`/v1/inventory/items/${id}/conversions`, { method: "PUT", body: { conversions } }), onSuccess: refresh });
  const updatePack = useMutation({ mutationFn: ({ itemId, id, name, base_quantity, last_price_minor, active }: { itemId: string; id: string; name: string; base_quantity: number; last_price_minor: number; active: boolean }) => api(`/v1/inventory/items/${itemId}/packs/${id}`, { method: "PATCH", body: { name, base_quantity, last_price_minor, active } }), onSuccess: async () => { await cache.invalidateQueries({ queryKey: ["inventory-packs"] }); } });
  const updateSupplier = useMutation({ mutationFn: ({ id, active }: { id: string; active: boolean }) => api(`/v1/inventory/suppliers/${id}`, { method: "PATCH", body: { active } }), onSuccess: refresh });
  const renameSupplier = useMutation({ mutationFn: ({ id, name, contact_name, email, phone }: { id: string; name: string; contact_name: string; email: string; phone: string }) => api(`/v1/inventory/suppliers/${id}`, { method: "PATCH", body: { name, contact_name, email, phone } }), onSuccess: refresh });
  const updateArea = useMutation({ mutationFn: ({ id, active, name }: { id: string; active?: boolean; name?: string }) => api(`/v1/inventory/storage-areas/${id}`, { method: "PATCH", body: { active, name } }), onSuccess: refresh });
  const assignArea = useMutation({ mutationFn: ({ itemId, areaId }: { itemId: string; areaId: string }) => api(`/v1/inventory/items/${itemId}/storage-area`, { method: "PUT", query: { location_id: locationId, storage_area_id: areaId } }), onSuccess: refresh });
  const setPar = useMutation({ mutationFn: ({ id, quantity }: { id: string; quantity: string }) => api(`/v1/inventory/items/${id}/par`, { method: "PUT", body: { location_id: locationId, par_base_quantity: Number(quantity) } }), onSuccess: refresh });
  return <Card><CardHeader overline="Catalog setup" title="Suppliers, packs, storage, and pars" /><div className="mt-4 grid gap-5 xl:grid-cols-2">
    <form className="grid gap-2 sm:grid-cols-2" onSubmit={(event) => { event.preventDefault(); if (supplierName.trim()) addSupplier.mutate(); }}><Input aria-label="Supplier name" placeholder="New supplier" value={supplierName} onChange={(event) => setSupplierName(event.target.value)} /><Input aria-label="Supplier contact" placeholder="Contact name" value={supplierContact} onChange={(event) => setSupplierContact(event.target.value)} /><Input aria-label="Supplier email" type="email" placeholder="Email" value={supplierEmail} onChange={(event) => setSupplierEmail(event.target.value)} /><div className="flex gap-2"><Input aria-label="Supplier phone" placeholder="Phone" value={supplierPhone} onChange={(event) => setSupplierPhone(event.target.value)} /><Button disabled={!supplierName.trim() || addSupplier.isPending} loading={addSupplier.isPending}>Add supplier</Button></div></form>
    <form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); if (areaName.trim()) addArea.mutate(); }}><Input aria-label="Storage area name" placeholder="New storage area" value={areaName} onChange={(event) => setAreaName(event.target.value)} /><Button disabled={!locationId || !areaName.trim() || addArea.isPending} loading={addArea.isPending}>Add area</Button></form>
    <form className="grid gap-2 sm:grid-cols-2" onSubmit={(event) => { event.preventDefault(); addPack.mutate(); }}><Select aria-label="Pack item" value={packItem} onChange={(event) => setPackItem(event.target.value)}><option value="">Choose item</option>{(items.data?.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</Select><Select aria-label="Pack supplier" value={packSupplier} onChange={(event) => setPackSupplier(event.target.value)}><option value="">Choose supplier</option>{(suppliers.data?.data ?? []).filter((supplier) => supplier.active).map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}</Select><Input aria-label="Pack name" placeholder="Pack label" value={packName} onChange={(event) => setPackName(event.target.value)} /><Input aria-label="Pack unit" placeholder="Purchase unit" value={packUnit} onChange={(event) => setPackUnit(event.target.value)} /><Input aria-label="Pack base quantity" inputMode="decimal" placeholder="Base quantity" value={packQuantity} onChange={(event) => setPackQuantity(event.target.value)} /><Button disabled={!packItem || !packSupplier || !packName || Number(packQuantity) <= 0 || addPack.isPending} loading={addPack.isPending}>Add pack</Button></form>
    <div className="space-y-2"><p className="eyebrow">Suppliers</p>{(suppliers.data?.data ?? []).map((supplier) => <div className="grid gap-2 rounded-lg border border-border p-3 text-sm sm:grid-cols-2" key={supplier.id}><Input aria-label={`Supplier name ${supplier.name}`} id={`supplier-name-${supplier.id}`} defaultValue={supplier.name} /><Input aria-label={`Supplier contact ${supplier.name}`} id={`supplier-contact-${supplier.id}`} defaultValue={supplier.contact_name ?? ""} placeholder="Contact" /><Input aria-label={`Supplier email ${supplier.name}`} id={`supplier-email-${supplier.id}`} defaultValue={supplier.email ?? ""} placeholder="Email" /><Input aria-label={`Supplier phone ${supplier.name}`} id={`supplier-phone-${supplier.id}`} defaultValue={supplier.phone ?? ""} placeholder="Phone" /><Button variant="outline" onClick={() => { const name = document.getElementById(`supplier-name-${supplier.id}`) as HTMLInputElement; const contact = document.getElementById(`supplier-contact-${supplier.id}`) as HTMLInputElement; const email = document.getElementById(`supplier-email-${supplier.id}`) as HTMLInputElement; const phone = document.getElementById(`supplier-phone-${supplier.id}`) as HTMLInputElement; if (name.value.trim()) renameSupplier.mutate({ id: supplier.id, name: name.value, contact_name: contact.value, email: email.value, phone: phone.value }); }}>Save supplier</Button><Button variant="ghost" onClick={() => updateSupplier.mutate({ id: supplier.id, active: !supplier.active })}>{supplier.active ? "Archive" : "Restore"}</Button></div>)}</div>
    <div className="space-y-2"><p className="eyebrow">Storage areas</p>{(areas.data?.data ?? []).map((area) => <div className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm" key={area.id}><Input aria-label={`Storage area name ${area.name}`} id={`area-name-${area.id}`} defaultValue={area.name} /><Button variant="outline" onClick={() => { const input = document.getElementById(`area-name-${area.id}`) as HTMLInputElement; if (input.value.trim()) updateArea.mutate({ id: area.id, name: input.value }); }}>Save</Button><Button variant="ghost" onClick={() => updateArea.mutate({ id: area.id, active: !area.active })}>{area.active ? "Archive" : "Restore"}</Button></div>)}</div>
    <div className="space-y-2 sm:col-span-2"><p className="eyebrow">Item details and par levels</p>{(items.data?.data ?? []).map((item) => <div className="grid gap-2 rounded-lg border border-border p-3 text-sm sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-center" key={item.id}><form className="grid gap-2 sm:grid-cols-2" onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); saveItem.mutate({ id: item.id, name: String(form.get("name")), category: String(form.get("category")) }); }}><Input name="name" aria-label={`Name for ${item.name}`} defaultValue={item.name} /><Input name="category" aria-label={`Category for ${item.name}`} defaultValue={item.category} placeholder="Category" /><Button type="submit" variant="outline" className="sm:col-span-2">Save item</Button></form><form className="flex gap-1" onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); setPar.mutate({ id: item.id, quantity: String(form.get("par") ?? "0") }); }}><Input name="par" inputMode="decimal" aria-label={`Par for ${item.name}`} className="w-24" defaultValue={item.par_base_quantity ?? ""} /><Button type="submit" variant="outline">Par</Button></form><Button variant="ghost" onClick={() => updateItem.mutate({ id: item.id, active: !item.active })}>{item.active ? "Archive" : "Restore"}</Button></div>)}</div>
    <div className="space-y-2 sm:col-span-2"><p className="eyebrow">Pack details</p>{(packs.data?.data ?? []).map((pack) => <div className="grid gap-2 rounded-lg border border-border p-3 sm:grid-cols-[1fr_1fr_1fr_auto_auto] sm:items-center" key={pack.id}><span className="text-sm font-medium">{pack.item_name}</span><Input aria-label={`Pack label ${pack.name}`} defaultValue={pack.name} id={`pack-name-${pack.id}`} /><Input aria-label={`Pack base quantity ${pack.name}`} inputMode="decimal" defaultValue={pack.base_quantity} id={`pack-qty-${pack.id}`} /><Input aria-label={`Pack price ${pack.name}`} inputMode="numeric" defaultValue={pack.last_price_minor ?? 0} id={`pack-price-${pack.id}`} /><div className="flex gap-1"><Button variant="outline" onClick={() => { const name = document.getElementById(`pack-name-${pack.id}`) as HTMLInputElement; const qty = document.getElementById(`pack-qty-${pack.id}`) as HTMLInputElement; const price = document.getElementById(`pack-price-${pack.id}`) as HTMLInputElement; updatePack.mutate({ itemId: pack.item_id, id: pack.id, name: name.value, base_quantity: Number(qty.value), last_price_minor: Number(price.value), active: pack.active }); }}>Save</Button><Button variant="ghost" onClick={() => updatePack.mutate({ itemId: pack.item_id, id: pack.id, name: pack.name, base_quantity: Number(pack.base_quantity), last_price_minor: pack.last_price_minor ?? 0, active: !pack.active })}>{pack.active ? "Archive" : "Restore"}</Button></div></div>)}</div>
    <div className="space-y-2 sm:col-span-2"><p className="eyebrow">Storage assignments and unit conversions</p>{(items.data?.data ?? []).filter((item) => item.active).map((item) => <div className="grid gap-3 rounded-lg border border-border p-3 sm:grid-cols-2" key={item.id}><div className="grid grid-cols-[1fr_1fr_auto] items-center gap-2"><span className="text-sm font-medium">{item.name}</span><Select aria-label={`Storage area for ${item.name}`} id={`area-${item.id}`}><option value="">Choose area</option>{(areas.data?.data ?? []).filter((area) => area.active).map((area) => <option key={area.id} value={area.id}>{area.name}</option>)}</Select><Button variant="outline" disabled={!locationId} onClick={() => { const select = document.getElementById(`area-${item.id}`) as HTMLSelectElement; if (select.value) assignArea.mutate({ itemId: item.id, areaId: select.value }); }}>Assign</Button></div><form className="grid grid-cols-[1fr_auto] gap-2" onSubmit={(event) => { event.preventDefault(); const input = new FormData(event.currentTarget).get("conversions"); try { const conversions = JSON.parse(String(input ?? "{}")) as Record<string, number>; setConversionError(""); saveConversions.mutate({ id: item.id, conversions }); } catch { setConversionError("Enter conversion factors as a JSON object, such as {\"kg\":1000}."); } }}><Input name="conversions" aria-label={`Unit conversions for ${item.name}`} defaultValue={item.dimension === "mass" ? '{"kg":1000}' : item.dimension === "volume" ? '{"l":1000}' : "{}"} /><Button type="submit" variant="outline">Save units</Button></form></div>)}{conversionError && <p className="text-xs text-destructive" role="alert">{conversionError}</p>}</div>
  </div></Card>;
}
