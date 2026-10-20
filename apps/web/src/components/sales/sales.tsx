"use client";

import { ReceiptText } from "lucide-react";
import { useMemo } from "react";
import {
  ChannelChart,
  LaborChart,
  SalesTrendChart,
  describeSeries,
  type LaborPoint,
  type TrendPoint,
} from "@/components/charts/charts";
import { FreshnessBadge } from "@/components/freshness-badge";
import { KpiTile, KpiTileSkeleton } from "@/components/kpi-tile";
import { PageHeader } from "@/components/page-header";
import { PosGate } from "@/components/pos-gate";
import { RangeControls } from "@/components/range-controls";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ChartSkeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useLocationScope } from "@/lib/location-scope";
import { formatInt, formatMoney, formatPct } from "@/lib/format";
import { useConnections, useDaily, useSummary } from "@/lib/queries";
import type { CompareMode, DailyRow, Location } from "@/lib/types";
import { useDateRange } from "@/lib/use-date-range";
import { DailyTable } from "./daily-table";

const COMPARE_LABEL: Record<CompareMode, string> = {
  previous_period: "Previous period",
  previous_year: "Same period last year",
};

export function Sales() {
  const { current } = useLocationScope();
  return (
    <>
      <PageHeader
        overline={current ? `Sales · ${current.name}` : "Sales"}
        title="Sales"
        description="Net sales, channel mix, and labor by day, against a period of your choice."
        badge={current ? <SalesFreshness location={current} /> : undefined}
      />
      <PosGate>{(loc) => <SalesContent location={loc} />}</PosGate>
    </>
  );
}

function SalesFreshness({ location }: { location: Location }) {
  const conns = useConnections();
  const r = useDateRange(location.timezone);
  const lid = conns.data && conns.data.length > 0 ? location.id : undefined;
  const q = useSummary(lid, r.range.from, r.range.to, r.compare);
  return q.data ? <FreshnessBadge freshness={q.data.freshness} timeZone={location.timezone} /> : null;
}

function SalesContent({ location }: { location: Location }) {
  const r = useDateRange(location.timezone);
  const summary = useSummary(location.id, r.range.from, r.range.to, r.compare);
  const daily = useDaily(location.id, r.range.from, r.range.to);
  const cmpDaily = useDaily(location.id, r.compareWindow.from, r.compareWindow.to);

  const days: DailyRow[] = useMemo(() => daily.data?.days ?? [], [daily.data]);
  const cmpDays: DailyRow[] = useMemo(() => cmpDaily.data?.days ?? [], [cmpDaily.data]);
  const cmpLabel = COMPARE_LABEL[r.compare];

  // Align the comparison series by day index so weekdays overlay (364-day shift
  // for previous year; same-length window for previous period).
  const trend: TrendPoint[] = useMemo(
    () =>
      days.map((d, i) => ({
        date: d.business_date,
        value: d.net_sales,
        compare: cmpDays[i]?.net_sales ?? null,
        compareDate: cmpDays[i]?.business_date,
      })),
    [days, cmpDays],
  );
  const labor: LaborPoint[] = useMemo(
    () =>
      days.map((d, i) => ({
        date: d.business_date,
        value: d.labor_pct,
        compare: cmpDays[i]?.labor_pct ?? null,
        compareDate: cmpDays[i]?.business_date,
      })),
    [days, cmpDays],
  );
  const hasSales = days.some((d) => d.net_sales > 0);
  const s = summary.data;

  return (
    <div className="space-y-6">
      <RangeControls
        preset={r.preset}
        range={r.range}
        today={r.today}
        onPreset={r.setPreset}
        onCustom={r.setCustom}
        compare={r.compare}
        compareWindow={r.compareWindow}
        onCompare={r.setCompare}
      />

      {/* KPIs */}
      {summary.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" role="status" aria-label="Loading KPIs">
          {Array.from({ length: 4 }, (_, i) => (
            <KpiTileSkeleton key={i} />
          ))}
        </div>
      ) : summary.isError ? (
        <Card>
          <ErrorState error={summary.error} onRetry={() => void summary.refetch()} retrying={summary.isFetching} />
        </Card>
      ) : s ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <KpiTile label="Net sales" value={formatMoney(s.current.net_sales)} delta={s.change.net_sales_pct} note={cmpLabel.toLowerCase()} />
            <KpiTile label="Orders" value={formatInt(s.current.orders)} delta={s.change.orders_pct} />
            <KpiTile label="Avg check" value={formatMoney(s.current.avg_check, 2)} delta={s.change.avg_check_pct} />
            <KpiTile label="Labor %" value={formatPct(s.current.labor_pct)} delta={s.change.labor_pct_pts} deltaKind="pts" invert />
          </div>
          <Card>
            <dl className="grid grid-cols-2 divide-x divide-y divide-border sm:grid-cols-4 sm:divide-y-0 [&>div]:p-5">
              <Mini label="Gross sales" now={s.current.gross_sales} prev={s.previous.gross_sales} />
              <Mini label="Discounts" now={s.current.discounts} prev={s.previous.discounts} />
              <Mini label="Returns" now={s.current.returns} prev={s.previous.returns} />
              <Mini label="Labor cost" now={s.current.labor_cost} prev={s.previous.labor_cost} />
            </dl>
          </Card>
        </>
      ) : null}

      {/* Charts + table share the daily query */}
      {daily.isPending ? (
        <Card>
          <CardHeader overline="Daily" title="Net sales" />
          <CardBody>
            <ChartSkeleton />
          </CardBody>
        </Card>
      ) : daily.isError ? (
        <Card>
          <ErrorState error={daily.error} onRetry={() => void daily.refetch()} retrying={daily.isFetching} />
        </Card>
      ) : !hasSales ? (
        <Card>
          <EmptyState
            icon={<ReceiptText />}
            title="No sales in this range"
            description="Try a wider date range. If your POS was just connected, history may still be backfilling."
          />
        </Card>
      ) : (
        <>
          <Card>
            <CardHeader overline="Daily" title="Net sales" description={`Compared with ${cmpLabel.toLowerCase()}`} />
            <CardBody>
              <SalesTrendChart
                data={trend}
                compareLabel={cmpDaily.data ? cmpLabel : undefined}
                label={describeSeries(
                  "Daily net sales",
                  trend.map((t) => ({ date: t.date, value: t.value })),
                )}
              />
            </CardBody>
          </Card>

          <div className="grid gap-6 xl:grid-cols-2">
            <Card>
              <CardHeader overline="Mix" title="Net sales by channel" />
              <CardBody>
                <ChannelChart days={days} label="Daily net sales stacked by channel: dine-in, pickup, delivery, online, other." />
              </CardBody>
            </Card>
            <Card>
              <CardHeader overline="Labor" title="Labor % by day" description="Labor cost as a share of net sales" />
              <CardBody>
                <LaborChart
                  data={labor}
                  compareLabel={cmpDaily.data ? cmpLabel : undefined}
                  label="Labor cost as a percentage of net sales, by day."
                />
              </CardBody>
            </Card>
          </div>

          <Card>
            <CardHeader overline="Detail" title="Daily breakdown" />
            <DailyTable days={days} totals={s?.current} />
          </Card>
        </>
      )}
    </div>
  );
}

function Mini({ label, now, prev }: { label: string; now: number; prev: number }) {
  return (
    <div>
      <dt className="text-xs text-foreground/50">{label}</dt>
      <dd className="mt-1 text-lg font-semibold tracking-[-0.04em] tabular">{formatMoney(now)}</dd>
      <dd className="text-xs text-foreground/40 tabular">was {formatMoney(prev)}</dd>
    </div>
  );
}
