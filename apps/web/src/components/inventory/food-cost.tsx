"use client";

import { useMemo, useState } from "react";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Select } from "@/components/ui/field";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import { useLocationScope } from "@/lib/location-scope";
import { useMutation, useQuery } from "@tanstack/react-query";

type Area = { id: string; name: string; active: boolean };
type CountSession = {
  id: string;
  status: string;
  submitted_at: string | null;
  storage_area_id: string;
  storage_area_name: string;
};
type FoodCostItem = {
  item_id: string;
  item_name: string;
  base_unit: string;
  opening_quantity: string | null;
  receipts_quantity: string;
  closing_quantity: string | null;
  actual_depletion_quantity: string | null;
  theoretical_usage_quantity: string | null;
  weighted_average_unit_cost_minor: string | null;
  actual_cost_minor: number | null;
  theoretical_cost_minor: number | null;
  variance_minor: number | null;
  variance_pct: string | null;
  recorded_waste_quantity: string;
  status: string;
};
type Report = {
  status: "complete" | "partial" | "unavailable";
  opening_count_date: string;
  closing_count_date: string;
  period_from: string;
  period_to: string;
  opening_submitted_at: string[];
  closing_submitted_at: string[];
  currency: string;
  totals_are_complete: boolean;
  actual_cost_minor: number | null;
  theoretical_cost_minor: number | null;
  variance_minor: number | null;
  variance_pct: string | null;
  coverage: {
    count_items_expected: number;
    count_items_covered: number;
    sales_lines: number;
    mapped_sales_lines: number;
    sales_quantity_coverage_pct: string | null;
    excluded_sales_lines: Record<string, number>;
    has_returns: boolean;
    orders_freshness: string;
    orders_as_of: string | null;
  };
  items: FoodCostItem[];
  limitations: string[];
};

const money = (minor: number | null, currency: string) =>
  minor == null
    ? "Unavailable"
    : new Intl.NumberFormat(undefined, { style: "currency", currency }).format(minor / 100);

export function FoodCostVariance() {
  const { current } = useLocationScope();
  const [opening, setOpening] = useState<Record<string, string>>({});
  const [closing, setClosing] = useState<Record<string, string>>({});
  const areas = useQuery({
    queryKey: ["inventory-storage-areas", current?.id],
    queryFn: () => api<{ data: Area[] }>("/v1/inventory/storage-areas", { query: { location_id: current?.id } }),
    enabled: !!current,
  });
  const counts = useQuery({
    queryKey: ["count-sessions", current?.id],
    queryFn: () => api<{ data: CountSession[] }>("/v1/inventory/count-sessions", { query: { location_id: current?.id } }),
    enabled: !!current,
  });
  const report = useMutation({
    mutationFn: () => api<Report>("/v1/inventory/food-cost-variance", {
      method: "POST",
      body: {
        location_id: current?.id,
        opening_count_session_ids: Object.values(opening),
        closing_count_session_ids: Object.values(closing),
      },
    }),
  });
  const activeAreas = useMemo(() => areas.data?.data.filter((area) => area.active !== false) ?? [], [areas.data]);
  const sessionsFor = (areaId: string) => (counts.data?.data ?? []).filter(
    (session) => session.storage_area_id === areaId && session.status === "submitted" && session.submitted_at,
  );
  const ready = activeAreas.length > 0 && activeAreas.every((area) => opening[area.id] && closing[area.id]);

  return (
    <div className="space-y-5">
      <PageHeader
        overline={current ? `Inventory · ${current.name}` : "Inventory"}
        title="Food cost variance"
        description="Compare counted inventory depletion with recipe use implied by completed sales. This is an operational estimate, not accounting COGS."
      />
      <Card>
        <CardHeader overline="Choose count anchors" title="Opening and closing counts" />
        <p className="mt-2 text-sm leading-relaxed text-foreground/60">
          Select one submitted count per active storage area for each anchor. Complete counts after close. The measured period starts the day after the opening count and ends on the closing count date.
        </p>
        {areas.isPending || counts.isPending ? <p className="mt-4 text-sm text-foreground/55">Loading submitted counts…</p> : areas.isError ? <ErrorState error={areas.error} onRetry={() => void areas.refetch()} retrying={areas.isFetching} /> : counts.isError ? <ErrorState error={counts.error} onRetry={() => void counts.refetch()} retrying={counts.isFetching} /> : activeAreas.length === 0 ? <EmptyState title="No active storage areas" description="Set up storage areas and assign inventory items before comparing food cost." /> : (
          <div className="mt-4 space-y-4">
            {activeAreas.map((area) => {
              const options = sessionsFor(area.id);
              return <div key={area.id} className="rounded-xl border border-border p-4">
                <p className="mb-3 font-semibold">{area.name}</p>
                {options.length < 2 && <p className="mb-3 text-xs text-foreground/55">Submit at least two counts for this area to compare a period.</p>}
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="space-y-1.5 text-xs font-semibold">Opening · after close
                    <Select aria-label={`${area.name} opening count`} value={opening[area.id] ?? ""} onChange={(event) => setOpening({ ...opening, [area.id]: event.target.value })}>
                      <option value="">Choose submitted count</option>
                      {options.filter((session) => session.id !== closing[area.id]).map((session) => <option key={session.id} value={session.id}>{new Date(session.submitted_at!).toLocaleString()}</option>)}
                    </Select>
                  </label>
                  <label className="space-y-1.5 text-xs font-semibold">Closing · after close
                    <Select aria-label={`${area.name} closing count`} value={closing[area.id] ?? ""} onChange={(event) => setClosing({ ...closing, [area.id]: event.target.value })}>
                      <option value="">Choose submitted count</option>
                      {options.filter((session) => session.id !== opening[area.id]).map((session) => <option key={session.id} value={session.id}>{new Date(session.submitted_at!).toLocaleString()}</option>)}
                    </Select>
                  </label>
                </div>
              </div>;
            })}
            <Button disabled={!ready || report.isPending} loading={report.isPending} onClick={() => report.mutate()}>Calculate variance</Button>
            {report.isError && <p role="alert" className="text-sm text-destructive">{report.error instanceof Error ? report.error.message : "Could not calculate food cost variance."}</p>}
          </div>
        )}
      </Card>
      {report.data && <>
        <Card>
          <CardHeader overline={`${report.data.period_from} through ${report.data.period_to} · ${report.data.status}`} title={report.data.totals_are_complete ? "Food cost comparison" : "Partial coverage"} />
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {[["Actual depletion", report.data.actual_cost_minor], ["Theoretical recipe use", report.data.theoretical_cost_minor], ["Variance", report.data.variance_minor]].map(([label, value]) => <div key={String(label)} className="rounded-xl bg-muted p-4"><p className="text-xs text-foreground/55">{label}</p><p className="mt-1 text-xl font-semibold tabular">{money(value as number | null, report.data.currency)}</p></div>)}
          </div>
          <p className="mt-4 text-sm text-foreground/60">Count coverage: {report.data.coverage.count_items_covered} of {report.data.coverage.count_items_expected} items · Sales quantity mapped: {report.data.coverage.sales_quantity_coverage_pct == null ? "Unavailable" : `${report.data.coverage.sales_quantity_coverage_pct}%`} · Orders: {report.data.coverage.orders_freshness}{report.data.coverage.has_returns ? " · returns present" : ""}</p>
          <p className="mt-1 text-xs text-foreground/50">Opening counts submitted {report.data.opening_submitted_at.map((at) => new Date(at).toLocaleString()).join(", ")}; closing counts submitted {report.data.closing_submitted_at.map((at) => new Date(at).toLocaleString()).join(", ")}. Orders synced through {report.data.coverage.orders_as_of ? new Date(report.data.coverage.orders_as_of).toLocaleString() : "no confirmed timestamp"}.</p>
          {report.data.totals_are_complete && report.data.variance_pct != null && <p className="mt-2 text-sm font-medium">Overall variance: {report.data.variance_pct}% of theoretical cost</p>}
          {Object.keys(report.data.coverage.excluded_sales_lines).length > 0 && <div className="mt-3 rounded-lg border border-border p-3"><p className="text-sm font-semibold">Excluded sales lines</p><ul className="mt-1 list-disc pl-5 text-sm text-foreground/65">{Object.entries(report.data.coverage.excluded_sales_lines).map(([reason, count]) => <li key={reason}>{reason.replaceAll("_", " ")}: {count}</li>)}</ul></div>}
          {!report.data.totals_are_complete && <p role="status" className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm">Covered item values below are partial subtotals. They are not a location total.</p>}
          {report.data.limitations.length > 0 && <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-foreground/65">{report.data.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>}
        </Card>
        <Card>
          <CardHeader overline="Ingredient detail" title="Inventory depletion and recipe use" />
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[780px] text-left text-sm">
              <thead className="text-xs text-foreground/55"><tr><th className="py-2 pr-4">Ingredient</th><th className="py-2 pr-4">Actual qty</th><th className="py-2 pr-4">Recipe qty</th><th className="py-2 pr-4">Waste subset</th><th className="py-2 pr-4">Actual cost</th><th className="py-2 pr-4">Recipe cost</th><th className="py-2 pr-4">Variance</th><th className="py-2">Coverage</th></tr></thead>
              <tbody className="divide-y divide-border">{report.data.items.map((item) => <tr key={item.item_id}><td className="py-3 pr-4 font-medium">{item.item_name}</td><td className="py-3 pr-4">{item.actual_depletion_quantity ?? "—"} {item.base_unit}</td><td className="py-3 pr-4">{item.theoretical_usage_quantity ?? "—"} {item.base_unit}</td><td className="py-3 pr-4">{item.recorded_waste_quantity} {item.base_unit}</td><td className="py-3 pr-4">{money(item.actual_cost_minor, report.data.currency)}</td><td className="py-3 pr-4">{money(item.theoretical_cost_minor, report.data.currency)}</td><td className="py-3 pr-4">{money(item.variance_minor, report.data.currency)}</td><td className="py-3 capitalize">{item.status.replaceAll("_", " ")}</td></tr>)}</tbody>
            </table>
          </div>
        </Card>
      </>}
    </div>
  );
}
