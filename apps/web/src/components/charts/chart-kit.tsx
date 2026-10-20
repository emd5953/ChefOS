"use client";

import type { ReactNode } from "react";
import { formatMoney } from "@/lib/format";

export const CHART = {
  ink: "#050505",
  blue: "#2861f5",
  blueSoft: "#8aa7f8",
  mute: "#9a9a9a",
  grid: "#ececec",
  channels: {
    dine_in: "#2861f5",
    pickup: "#050505",
    delivery: "#8aa7f8",
    online: "#9a9a9a",
    other: "#d4d4d4",
  },
} as const;

export const axisProps = {
  tickLine: false,
  axisLine: false,
  tick: { fill: "#747474", fontSize: 11 },
} as const;

export function ChartFrame({
  label,
  height = 280,
  legend,
  children,
}: {
  label: string;
  height?: number;
  legend?: ReactNode;
  children: ReactNode;
}) {
  return (
    <figure aria-label={label} className="m-0">
      <div style={{ height }} className="w-full min-w-0">
        {children}
      </div>
      {legend && <figcaption className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5">{legend}</figcaption>}
    </figure>
  );
}

export function LegendItem({ color, label, dashed }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-foreground/60">
      <span
        aria-hidden
        className="inline-block h-0 w-4 border-t-2"
        style={{ borderColor: color, borderStyle: dashed ? "dashed" : "solid" }}
      />
      {label}
    </span>
  );
}

export type SeriesDef = {
  key: string;
  label: string;
  color: string;
  format: (v: number) => string;
};

type TipProps = {
  active?: boolean;
  payload?: ReadonlyArray<{ dataKey?: string | number; value?: unknown; payload?: Record<string, unknown> }>;
  label?: string | number;
  series: SeriesDef[];
  title: (label: string | number | undefined, row: Record<string, unknown> | undefined) => string;
  subtitle?: (row: Record<string, unknown> | undefined) => string | undefined;
};

export function ChartTip({ active, payload, label, series, title, subtitle }: TipProps) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload;
  const rows = series
    .map((s) => {
      const hit = payload.find((p) => p.dataKey === s.key);
      return hit && typeof hit.value === "number" ? { s, value: hit.value } : null;
    })
    .filter((x): x is { s: SeriesDef; value: number } => x !== null);
  if (rows.length === 0) return null;
  const sub = subtitle?.(row);
  return (
    <div className="min-w-40 rounded-xl border border-border bg-background px-3.5 py-3 text-xs shadow-lg">
      <p className="font-semibold tracking-[-0.01em]">{title(label, row)}</p>
      {sub && <p className="mt-0.5 text-foreground/45">{sub}</p>}
      <ul className="mt-2 space-y-1">
        {rows.map(({ s, value }) => (
          <li key={s.key} className="flex items-center justify-between gap-4">
            <span className="inline-flex items-center gap-1.5 text-foreground/60">
              <span aria-hidden className="size-2 rounded-full" style={{ background: s.color }} />
              {s.label}
            </span>
            <span className="font-semibold tabular">{s.format(value)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export const money = (v: number) => formatMoney(v, 2);
