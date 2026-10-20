"use client";

import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatDate, formatHour, formatMoney, formatMoneyCompact, formatPct, formatWeekday } from "@/lib/format";
import { useReducedMotion } from "@/lib/use-reduced-motion";
import type { Channel, DailyRow } from "@/lib/types";
import { axisProps, CHART, ChartFrame, ChartTip, LegendItem, money, type SeriesDef } from "./chart-kit";

const margin = { top: 8, right: 8, bottom: 0, left: 0 };

// ---------- Net sales trend (current + optional comparison) ----------

export type TrendPoint = {
  date: string;
  value: number;
  compare?: number | null;
  compareDate?: string;
};

export function SalesTrendChart({
  data,
  label,
  compareLabel,
  height = 300,
}: {
  data: TrendPoint[];
  label: string;
  compareLabel?: string;
  height?: number;
}) {
  const reduced = useReducedMotion();
  const hasCompare = !!compareLabel && data.some((d) => d.compare != null);
  const series: SeriesDef[] = [
    { key: "value", label: "Net sales", color: CHART.blue, format: money },
    ...(hasCompare ? [{ key: "compare", label: compareLabel ?? "Comparison", color: CHART.mute, format: money }] : []),
  ];
  return (
    <ChartFrame
      label={label}
      height={height}
      legend={
        <>
          <LegendItem color={CHART.blue} label="Net sales" />
          {hasCompare && <LegendItem color={CHART.mute} label={compareLabel ?? "Comparison"} dashed />}
        </>
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={margin}>
          <CartesianGrid vertical={false} stroke={CHART.grid} />
          <XAxis dataKey="date" {...axisProps} tickFormatter={(d: string) => formatDate(d)} minTickGap={28} />
          <YAxis {...axisProps} width={48} tickFormatter={(v: number) => formatMoneyCompact(v)} />
          <Tooltip
            cursor={{ stroke: CHART.mute, strokeDasharray: "3 3" }}
            content={
              <ChartTip
                series={series}
                title={(l) => `${formatWeekday(String(l))}, ${formatDate(String(l))}`}
                subtitle={(row) =>
                  hasCompare && typeof row?.compareDate === "string" ? `vs ${formatDate(row.compareDate)}` : undefined
                }
              />
            }
          />
          <Area
            type="monotone"
            dataKey="value"
            stroke={CHART.blue}
            strokeWidth={2}
            fill={CHART.blue}
            fillOpacity={0.08}
            dot={false}
            activeDot={{ r: 4 }}
            isAnimationActive={!reduced}
          />
          {hasCompare && (
            <Line
              type="monotone"
              dataKey="compare"
              stroke={CHART.mute}
              strokeWidth={1.75}
              strokeDasharray="5 4"
              dot={false}
              activeDot={{ r: 3 }}
              connectNulls
              isAnimationActive={!reduced}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------- Hourly bars (today) ----------

export function HourlyChart({
  data,
  label,
}: {
  data: Array<{ hour: number; net_sales: number; orders: number }>;
  label: string;
}) {
  const reduced = useReducedMotion();
  const byHour = new Map(data.map((d) => [d.hour, d]));
  const active = data.filter((d) => d.net_sales > 0 || d.orders > 0).map((d) => d.hour);
  const start = Math.min(9, ...(active.length ? active : [9]));
  const end = Math.max(21, ...(active.length ? active : [21]));
  const rows = Array.from({ length: end - start + 1 }, (_, i) => {
    const hour = start + i;
    const d = byHour.get(hour);
    return { hour, net_sales: d?.net_sales ?? 0, orders: d?.orders ?? 0 };
  });
  const series: SeriesDef[] = [
    { key: "net_sales", label: "Net sales", color: CHART.blue, format: money },
    { key: "orders", label: "Orders", color: CHART.ink, format: (v) => String(v) },
  ];
  return (
    <ChartFrame label={label} height={200}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={margin} barCategoryGap="22%">
          <CartesianGrid vertical={false} stroke={CHART.grid} />
          <XAxis dataKey="hour" {...axisProps} tickFormatter={(h: number) => formatHour(h)} interval="preserveStartEnd" minTickGap={14} />
          <YAxis {...axisProps} width={44} tickFormatter={(v: number) => formatMoneyCompact(v)} />
          <Tooltip
            cursor={{ fill: "rgb(5 5 5 / 0.04)" }}
            content={<ChartTip series={series} title={(l) => `${formatHour(Number(l))} hour`} />}
          />
          <Bar dataKey="net_sales" fill={CHART.blue} radius={[5, 5, 0, 0]} isAnimationActive={!reduced} />
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------- Channel mix (stacked daily) ----------

export const CHANNEL_LABEL: Record<Channel, string> = {
  dine_in: "Dine-in",
  pickup: "Pickup",
  delivery: "Delivery",
  online: "Online",
  other: "Other",
};
const CHANNELS = Object.keys(CHANNEL_LABEL) as Channel[];

export function ChannelChart({ days, label }: { days: DailyRow[]; label: string }) {
  const reduced = useReducedMotion();
  const rows = days.map((d) => ({ date: d.business_date, ...d.channels }));
  const series: SeriesDef[] = CHANNELS.map((c) => ({
    key: c,
    label: CHANNEL_LABEL[c],
    color: CHART.channels[c],
    format: money,
  }));
  return (
    <ChartFrame
      label={label}
      height={280}
      legend={CHANNELS.map((c) => (
        <LegendItem key={c} color={CHART.channels[c]} label={CHANNEL_LABEL[c]} />
      ))}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={margin} barCategoryGap="18%">
          <CartesianGrid vertical={false} stroke={CHART.grid} />
          <XAxis dataKey="date" {...axisProps} tickFormatter={(d: string) => formatDate(d)} minTickGap={28} />
          <YAxis {...axisProps} width={48} tickFormatter={(v: number) => formatMoneyCompact(v)} />
          <Tooltip
            cursor={{ fill: "rgb(5 5 5 / 0.04)" }}
            content={<ChartTip series={[...series].reverse()} title={(l) => formatDate(String(l), true)} />}
          />
          {CHANNELS.map((c) => (
            <Bar key={c} dataKey={c} stackId="channels" fill={CHART.channels[c]} isAnimationActive={!reduced} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------- Labor % by day ----------

export type LaborPoint = { date: string; value: number | null; compare?: number | null; compareDate?: string };

export function LaborChart({
  data,
  label,
  compareLabel,
}: {
  data: LaborPoint[];
  label: string;
  compareLabel?: string;
}) {
  const reduced = useReducedMotion();
  const hasCompare = !!compareLabel && data.some((d) => d.compare != null);
  const fmt = (v: number) => formatPct(v);
  const series: SeriesDef[] = [
    { key: "value", label: "Labor %", color: CHART.ink, format: fmt },
    ...(hasCompare ? [{ key: "compare", label: compareLabel ?? "Comparison", color: CHART.mute, format: fmt }] : []),
  ];
  return (
    <ChartFrame
      label={label}
      height={240}
      legend={
        <>
          <LegendItem color={CHART.ink} label="Labor %" />
          {hasCompare && <LegendItem color={CHART.mute} label={compareLabel ?? "Comparison"} dashed />}
        </>
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={margin}>
          <CartesianGrid vertical={false} stroke={CHART.grid} />
          <XAxis dataKey="date" {...axisProps} tickFormatter={(d: string) => formatDate(d)} minTickGap={28} />
          <YAxis {...axisProps} width={40} tickFormatter={(v: number) => `${Math.round(v)}%`} domain={[0, "auto"]} />
          <Tooltip
            cursor={{ stroke: CHART.mute, strokeDasharray: "3 3" }}
            content={
              <ChartTip
                series={series}
                title={(l) => formatDate(String(l), true)}
                subtitle={(row) =>
                  hasCompare && typeof row?.compareDate === "string" ? `vs ${formatDate(row.compareDate)}` : undefined
                }
              />
            }
          />
          <Line type="monotone" dataKey="value" stroke={CHART.ink} strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={!reduced} />
          {hasCompare && (
            <Line
              type="monotone"
              dataKey="compare"
              stroke={CHART.mute}
              strokeWidth={1.75}
              strokeDasharray="5 4"
              dot={false}
              connectNulls
              isAnimationActive={!reduced}
            />
          )}
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

/** Screen-reader summary for a net-sales series. */
export function describeSeries(name: string, days: Array<{ date: string; value: number }>): string {
  if (days.length === 0) return `${name}: no data`;
  const total = days.reduce((a, d) => a + d.value, 0);
  const peak = days.reduce((a, d) => (d.value > a.value ? d : a), days[0]!);
  const first = days[0]!;
  const last = days[days.length - 1]!;
  return `${name}, ${formatDate(first.date)} to ${formatDate(last.date)}. Total ${formatMoney(total)}. Peak ${formatMoney(peak.value)} on ${formatDate(peak.date)}.`;
}
