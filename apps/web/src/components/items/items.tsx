"use client";

import { AlertTriangle, UtensilsCrossed } from "lucide-react";
import Link from "next/link";
import { FreshnessBadge } from "@/components/freshness-badge";
import { PageHeader } from "@/components/page-header";
import { PosGate } from "@/components/pos-gate";
import { RangeControls } from "@/components/range-controls";
import { Card, CardHeader } from "@/components/ui/card";
import { TableSkeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { formatInt, formatMoney, formatPct } from "@/lib/format";
import { useLocationScope } from "@/lib/location-scope";
import { useConnections, useItems, useToday } from "@/lib/queries";
import type { Location } from "@/lib/types";
import { useDateRange } from "@/lib/use-date-range";

export function Items() {
  const { current } = useLocationScope();
  return (
    <>
      <PageHeader
        overline={current ? `Menu items · ${current.name}` : "Menu items"}
        title="Menu items"
        description="What sold, how much of it, and how much each item contributes."
        badge={current ? <ItemsFreshness location={current} /> : undefined}
      />
      <PosGate>{(loc) => <ItemsContent location={loc} />}</PosGate>
    </>
  );
}

// The items endpoint carries no freshness; reuse the orders-stream freshness
// from /commerce/today (same cache entry the Overview uses).
function ItemsFreshness({ location }: { location: Location }) {
  const conns = useConnections();
  const q = useToday(conns.data && conns.data.length > 0 ? location.id : undefined);
  return q.data ? <FreshnessBadge freshness={q.data.freshness} timeZone={location.timezone} /> : null;
}

function ItemsContent({ location }: { location: Location }) {
  const r = useDateRange(location.timezone);
  const q = useItems(location.id, r.range.from, r.range.to);
  const { href } = useLocationScope();
  const items = q.data?.items ?? [];
  const maxShare = Math.max(1, ...items.map((i) => i.share_pct));
  const unmapped = q.data?.unmapped_net_sales ?? 0;

  return (
    <div className="space-y-6">
      <RangeControls
        preset={r.preset}
        range={r.range}
        today={r.today}
        onPreset={r.setPreset}
        onCustom={r.setCustom}
      />

      {unmapped > 0 && (
        <div className="flex items-start gap-3 rounded-2xl border border-warning/30 bg-warning/8 px-4 py-3.5 text-[13px] text-foreground/80">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
          <p>
            <strong className="font-semibold">{formatMoney(unmapped)}</strong> in unmapped sales isn&rsquo;t attributed to
            any item above.{" "}
            <Link
              href={href("/app/integrations")}
              className="font-semibold underline underline-offset-4 hover:text-foreground"
            >
              Review mapping issues
            </Link>
          </p>
        </div>
      )}

      <Card>
        <CardHeader overline="Ranked by net sales" title="Top items" />
        <div className="mt-4">
          {q.isPending ? (
            <TableSkeleton rows={8} cols={4} />
          ) : q.isError ? (
            <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
          ) : items.length === 0 ? (
            <EmptyState
              icon={<UtensilsCrossed />}
              title="No item sales in this range"
              description="Try a wider date range, or check that the catalog stream has synced."
            />
          ) : (
            <div className="overflow-x-auto scrollbar-thin" tabIndex={0} role="region" aria-label="Menu items table">
              <table className="w-full min-w-[640px] border-separate border-spacing-0 text-[13px]">
                <thead>
                  <tr className="text-left">
                    {["Item", "Category", "Qty", "Net sales", "Share"].map((h, i) => (
                      <th
                        key={h}
                        scope="col"
                        className={`eyebrow border-b border-border px-3 py-2 text-[10px] first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6 ${i === 2 || i === 3 ? "text-right" : ""} ${i === 4 ? "w-[34%]" : ""}`}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, idx) => (
                    <tr key={`${it.name}-${idx}`} className="group">
                      <td className="border-b border-border/70 px-3 py-3 pl-5 font-medium transition-colors group-hover:bg-muted/70 sm:pl-6">
                        {it.name}
                      </td>
                      <td className="border-b border-border/70 px-3 py-3 text-foreground/60 transition-colors group-hover:bg-muted/70">
                        {it.category || "—"}
                      </td>
                      <td className="border-b border-border/70 px-3 py-3 text-right tabular transition-colors group-hover:bg-muted/70">
                        {formatInt(it.quantity)}
                      </td>
                      <td className="border-b border-border/70 px-3 py-3 text-right font-medium tabular transition-colors group-hover:bg-muted/70">
                        {formatMoney(it.net_sales, 2)}
                      </td>
                      <td className="border-b border-border/70 px-3 py-3 pr-5 transition-colors group-hover:bg-muted/70 sm:pr-6">
                        <div className="flex items-center gap-3">
                          <div
                            role="meter"
                            aria-label={`${it.name} share of sales`}
                            aria-valuemin={0}
                            aria-valuemax={100}
                            aria-valuenow={Math.round(it.share_pct)}
                            className="h-1.5 flex-1 overflow-hidden rounded-full bg-foreground/[0.06]"
                          >
                            <div
                              className="h-full rounded-full bg-accent"
                              style={{ width: `${Math.max(2, (it.share_pct / maxShare) * 100)}%` }}
                            />
                          </div>
                          <span className="w-12 text-right tabular text-foreground/70">{formatPct(it.share_pct)}</span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
