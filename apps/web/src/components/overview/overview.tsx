"use client";

import { Clock, ReceiptText, ShoppingBag, Wallet } from "lucide-react";
import { useMemo } from "react";
import { HourlyChart, SalesTrendChart, describeSeries } from "@/components/charts/charts";
import { FreshnessBadge } from "@/components/freshness-badge";
import { KpiTile, KpiTileSkeleton } from "@/components/kpi-tile";
import { PageHeader } from "@/components/page-header";
import { PosGate } from "@/components/pos-gate";
import { Dot } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ChartSkeleton, Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { addDays, todayIn } from "@/lib/dates";
import { formatClock, formatDate, formatInt, formatMoney, formatPct, formatRelative } from "@/lib/format";
import { useConnections, useDaily, useSummary, useToday } from "@/lib/queries";
import type { Freshness, Location } from "@/lib/types";
import { useNow } from "@/lib/use-now";
import { useLocationScope } from "@/lib/location-scope";

export function Overview() {
  const { current } = useLocationScope();
  return <OverviewBody location={current} />;
}

function OverviewBody({ location }: { location: Location | undefined }) {
  // Freshness for the header comes from the data views below; they share query
  // caches with the gate children, so reading them here costs no extra requests.
  const tz = location?.timezone;
  const conns = useConnections();
  const lid = conns.data && conns.data.length > 0 ? location?.id : undefined;
  const todayStr = todayIn(tz);
  const today = useToday(lid, true);
  const summary = useSummary(lid, addDays(todayStr, -6), todayStr, "previous_period");
  const daily = useDaily(lid, addDays(todayStr, -29), todayStr);
  const freshness: Freshness[] = [
    ...(today.data?.freshness ?? []),
    ...(summary.data?.freshness ?? []),
    ...(daily.data?.freshness ?? []),
  ];
  const hasAny = !!(today.data || summary.data || daily.data);

  return (
    <>
      <PageHeader
        overline={location ? `Overview · ${location.name}` : "Overview"}
        title="How the house is doing"
        description="Today as it happens, and the last seven days against the week before."
        badge={hasAny ? <FreshnessBadge freshness={freshness} timeZone={tz} /> : undefined}
      />
      <PosGate>{(loc) => <OverviewContent location={loc} />}</PosGate>
    </>
  );
}

function OverviewContent({ location }: { location: Location }) {
  const tz = location.timezone;
  const today = useMemo(() => todayIn(tz), [tz]);
  const weekFrom = addDays(today, -6);
  const monthFrom = addDays(today, -29);

  return (
    <div className="space-y-6">
      <TodayCard location={location} />
      <KpiRow locationId={location.id} from={weekFrom} to={today} />
      <TrendCard locationId={location.id} from={monthFrom} to={today} />
    </div>
  );
}

// ---------- Today ----------

function TodayCard({ location }: { location: Location }) {
  const q = useToday(location.id, true);
  const now = useNow();
  const d = q.data;

  return (
    <Card className="overflow-hidden">
      <CardHeader
        overline="Today"
        title={
          <span className="inline-flex items-center gap-2.5">
            {d ? formatDate(d.business_date, true) : "Today"}
            {d && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-success/10 px-2 py-1 text-[11px] font-semibold text-success">
                <Dot tone="success" pulse /> Live
              </span>
            )}
          </span>
        }
        description="Refreshes every 30 seconds."
      />
      {q.isPending ? (
        <CardBody className="grid gap-8 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
          <div className="space-y-5" role="status" aria-label="Loading today">
            <Skeleton className="h-12 w-48" />
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-5 w-40" />
          </div>
          <ChartSkeleton height={200} />
        </CardBody>
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : d && d.orders === 0 && d.net_sales === 0 ? (
        <EmptyState
          icon={<ReceiptText />}
          title="No orders yet today"
          description="The first ticket of the day will show up here within a minute or two of closing."
        />
      ) : d ? (
        <CardBody className="grid gap-8 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
          <div>
            <p className="eyebrow">Net sales</p>
            <p className="mt-2 text-[56px] font-semibold leading-none tracking-[-0.06em] tabular">
              {formatMoney(d.net_sales)}
            </p>
            <dl className="mt-6 grid grid-cols-2 gap-4">
              <Stat icon={<ShoppingBag />} label="Orders" value={formatInt(d.orders)} />
              <Stat icon={<Wallet />} label="Avg check" value={formatMoney(d.avg_check, 2)} />
              <Stat
                icon={<Clock />}
                label="Last order"
                value={d.last_order_at ? formatClock(d.last_order_at, location.timezone) : "—"}
                sub={d.last_order_at ? formatRelative(d.last_order_at, now) : undefined}
                wide
              />
            </dl>
          </div>
          <div className="min-w-0">
            <p className="eyebrow mb-3">Net sales by hour</p>
            <HourlyChart
              data={d.by_hour}
              label={`Net sales by hour today. Total ${formatMoney(d.net_sales)} across ${d.orders} orders.`}
            />
          </div>
        </CardBody>
      ) : null}
    </Card>
  );
}

function Stat({
  icon,
  label,
  value,
  sub,
  wide,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
  wide?: boolean;
}) {
  return (
    <div className={wide ? "col-span-2" : undefined}>
      <dt className="flex items-center gap-1.5 text-xs text-foreground/50 [&_svg]:size-3.5">
        {icon} {label}
      </dt>
      <dd className="mt-1 text-xl font-semibold tracking-[-0.04em] tabular">
        {value}
        {sub && <span className="ml-2 text-xs font-medium tracking-normal text-foreground/45">{sub}</span>}
      </dd>
    </div>
  );
}

// ---------- KPI row ----------

function KpiRow({ locationId, from, to }: { locationId: string; from: string; to: string }) {
  const q = useSummary(locationId, from, to, "previous_period");
  const s = q.data;

  if (q.isPending) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" role="status" aria-label="Loading KPIs">
        {Array.from({ length: 4 }, (_, i) => (
          <KpiTileSkeleton key={i} />
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
  if (!s) return null;

  return (
    <section aria-labelledby="kpi-heading">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 id="kpi-heading" className="eyebrow">
          Last 7 days &middot; vs previous 7
        </h2>
        <span className="text-xs text-foreground/45">
          {formatDate(s.period.from)} &ndash; {formatDate(s.period.to)}
        </span>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiTile label="Net sales" value={formatMoney(s.current.net_sales)} delta={s.change.net_sales_pct} />
        <KpiTile label="Orders" value={formatInt(s.current.orders)} delta={s.change.orders_pct} />
        <KpiTile label="Avg check" value={formatMoney(s.current.avg_check, 2)} delta={s.change.avg_check_pct} />
        <KpiTile
          label="Labor %"
          value={formatPct(s.current.labor_pct)}
          delta={s.change.labor_pct_pts}
          deltaKind="pts"
          invert
        />
      </div>
    </section>
  );
}

// ---------- 30-day trend ----------

function TrendCard({ locationId, from, to }: { locationId: string; from: string; to: string }) {
  const q = useDaily(locationId, from, to);
  const days = q.data?.days;
  const points = useMemo(
    () => (days ?? []).map((d) => ({ date: d.business_date, value: d.net_sales })),
    [days],
  );
  const total = points.reduce((a, p) => a + p.value, 0);

  return (
    <Card>
      <CardHeader
        overline="Last 30 days"
        title="Net sales"
        description={points.length > 0 && total > 0 ? `${formatMoney(total)} over ${points.length} days` : undefined}
      />
      <CardBody>
        {q.isPending ? (
          <ChartSkeleton />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : total === 0 ? (
          <EmptyState
            icon={<ReceiptText />}
            title="No sales in the last 30 days"
            description="Once your POS finishes its first sync, daily sales will chart here."
          />
        ) : (
          <SalesTrendChart data={points} label={describeSeries("Daily net sales, last 30 days", points)} />
        )}
      </CardBody>
    </Card>
  );
}
