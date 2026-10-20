"use client";

import { ArrowDown, ArrowUp } from "lucide-react";
import { useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import { formatDate, formatInt, formatMoney, formatPct, formatWeekday } from "@/lib/format";
import type { DailyRow, Totals } from "@/lib/types";

type Col = {
  key: string;
  label: string;
  value: (d: DailyRow) => number | string | null;
  render: (d: DailyRow) => string;
  numeric?: boolean;
};

const COLS: Col[] = [
  {
    key: "date",
    label: "Date",
    value: (d) => d.business_date,
    render: (d) => `${formatWeekday(d.business_date)}, ${formatDate(d.business_date)}`,
  },
  { key: "net", label: "Net sales", value: (d) => d.net_sales, render: (d) => formatMoney(d.net_sales, 2), numeric: true },
  { key: "orders", label: "Orders", value: (d) => d.orders, render: (d) => formatInt(d.orders), numeric: true },
  { key: "avg", label: "Avg check", value: (d) => d.avg_check, render: (d) => formatMoney(d.avg_check, 2), numeric: true },
  { key: "labor", label: "Labor cost", value: (d) => d.labor_cost, render: (d) => formatMoney(d.labor_cost, 2), numeric: true },
  { key: "laborpct", label: "Labor %", value: (d) => d.labor_pct, render: (d) => formatPct(d.labor_pct), numeric: true },
  { key: "disc", label: "Discounts", value: (d) => d.discounts, render: (d) => formatMoney(d.discounts, 2), numeric: true },
  { key: "ret", label: "Returns", value: (d) => d.returns, render: (d) => formatMoney(d.returns, 2), numeric: true },
];

export function DailyTable({ days, totals }: { days: DailyRow[]; totals?: Totals }) {
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: "date", dir: "desc" });

  const rows = useMemo(() => {
    const col = COLS.find((c) => c.key === sort.key) ?? COLS[0]!;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...days].sort((a, b) => {
      const av = col.value(a);
      const bv = col.value(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1; // nulls always last
      if (bv == null) return -1;
      return (av < bv ? -1 : av > bv ? 1 : 0) * sign;
    });
  }, [days, sort]);

  const toggle = (key: string) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "date" ? "desc" : "desc" }));

  return (
    <div className="mt-4 max-h-[560px] overflow-auto scrollbar-thin" tabIndex={0} role="region" aria-label="Daily sales table">
      <table className="w-full min-w-[820px] border-separate border-spacing-0 text-[13px]">
        <thead>
          <tr>
            {COLS.map((c) => {
              const active = sort.key === c.key;
              return (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                  className={cn(
                    "sticky top-0 z-10 border-b border-border bg-background/95 px-3 py-2 font-medium backdrop-blur-sm first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6",
                    c.numeric ? "text-right" : "text-left",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => toggle(c.key)}
                    className={cn(
                      "eyebrow inline-flex items-center gap-1 rounded-md py-1 text-[10px] transition-[color,transform] duration-150 hover:text-foreground active:scale-[0.96]",
                      active && "text-foreground",
                      c.numeric && "flex-row-reverse",
                    )}
                  >
                    {c.label}
                    {active ? (
                      sort.dir === "asc" ? <ArrowUp className="size-3" aria-hidden /> : <ArrowDown className="size-3" aria-hidden />
                    ) : (
                      <span className="size-3" aria-hidden />
                    )}
                    <span className="sr-only">
                      {active ? `, sorted ${sort.dir === "asc" ? "ascending" : "descending"}` : ", click to sort"}
                    </span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.business_date} className="group">
              {COLS.map((c, i) => (
                <td
                  key={c.key}
                  className={cn(
                    "border-b border-border/70 px-3 py-2.5 tabular transition-colors duration-100 group-hover:bg-muted/70 first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6",
                    c.numeric ? "text-right" : "text-left font-medium",
                    i > 1 && c.key !== "net" && "text-foreground/70",
                  )}
                >
                  {c.render(d)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {totals && (
          <tfoot>
            <tr className="font-semibold">
              <td className="sticky bottom-0 bg-background px-3 py-3 first:pl-5 sm:first:pl-6">Total</td>
              <td className="sticky bottom-0 bg-background px-3 py-3 text-right tabular">{formatMoney(totals.net_sales, 2)}</td>
              <td className="sticky bottom-0 bg-background px-3 py-3 text-right tabular">{formatInt(totals.orders)}</td>
              <td className="sticky bottom-0 bg-background px-3 py-3 text-right tabular">{formatMoney(totals.avg_check, 2)}</td>
              <td className="sticky bottom-0 bg-background px-3 py-3 text-right tabular">{formatMoney(totals.labor_cost, 2)}</td>
              <td className="sticky bottom-0 bg-background px-3 py-3 text-right tabular">{formatPct(totals.labor_pct)}</td>
              <td className="sticky bottom-0 bg-background px-3 py-3 text-right tabular">{formatMoney(totals.discounts, 2)}</td>
              <td className="sticky bottom-0 bg-background px-3 py-3 pr-5 text-right tabular sm:pr-6">{formatMoney(totals.returns, 2)}</td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
