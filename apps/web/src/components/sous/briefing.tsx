"use client";

import { ChevronRight, TriangleAlert } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Badge, Dot, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import {
  formatClock,
  formatDate,
  formatDateTime,
  formatHours,
  formatInt,
  formatMoney,
  formatPct,
  formatRelative,
  formatShiftRange,
  formatWeekday,
} from "@/lib/format";
import { useBriefing } from "@/lib/queries";
import type { Briefing, BriefingSectionStatus, FreshnessStatus } from "@/lib/types";
import { useNow } from "@/lib/use-now";
import { formatEvidence } from "./answer";

const STREAM_LABEL: Record<Briefing["freshness"][number]["stream"], string> = {
  orders: "Sales",
  timecards: "Labor",
  catalog: "Menu",
  team_members: "Team",
};
const FRESH_TONE: Record<FreshnessStatus, Tone> = {
  fresh: "success",
  stale: "warning",
  failing: "danger",
  not_connected: "neutral",
};
const SHIFTS_SHOWN = 8;

/** Deterministic daily briefing: works whether or not the assistant model is running. */
export function BriefingCard({ locationId }: { locationId: string }) {
  const [date, setDate] = useState(""); // "" = today at the location
  const q = useBriefing(locationId, date);
  const b = q.data;

  return (
    <Card className="mx-auto mb-6 w-full max-w-3xl" aria-busy={q.isFetching || undefined}>
      <CardHeader
        overline={b ? `Daily briefing · ${b.location_name}` : "Daily briefing"}
        title={b ? `${formatWeekday(b.business_date)}, ${formatDate(b.business_date, true)}` : "Today"}
        description={
          b
            ? `Results for ${formatWeekday(b.results_date)} ${formatDate(b.results_date)} · staffing for this day · ${b.timezone}`
            : "Yesterday’s results, today’s staffing, and items below par."
        }
        action={
          <div className="flex items-center gap-2">
            <label htmlFor="briefing-date" className="sr-only">
              Briefing date
            </label>
            <Input
              id="briefing-date"
              type="date"
              value={date || b?.business_date || ""}
              onChange={(e) => setDate(e.target.value)}
              className="h-9 w-[150px]"
            />
            {date && (
              <Button variant="outline" size="sm" onClick={() => setDate("")}>
                Today
              </Button>
            )}
          </div>
        }
      />
      <CardBody className="space-y-4">
        {q.isPending ? (
          <BriefingSkeleton />
        ) : q.isError && !b ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} className="py-6" />
        ) : b ? (
          <BriefingView b={b} />
        ) : null}
      </CardBody>
    </Card>
  );
}

function BriefingSkeleton() {
  return (
    <div aria-hidden className="space-y-3">
      <Skeleton className="h-8 w-full rounded-xl" />
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-4 w-2/3" />
      <div className="grid gap-3 sm:grid-cols-2">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
    </div>
  );
}

function FreshnessStrip({ b }: { b: Briefing }) {
  const now = useNow();
  if (b.freshness.length === 0) return null;
  return (
    <ul aria-label="Data freshness" className="flex flex-wrap gap-2">
      {b.freshness.map((f) => {
        const tone = FRESH_TONE[f.status];
        const text =
          f.status === "fresh"
            ? `updated ${formatRelative(f.data_as_of, now)}`
            : f.status === "stale"
              ? f.data_as_of
                ? `stale · as of ${formatDateTime(f.data_as_of, b.timezone)}`
                : "stale · never synced"
              : f.status === "failing"
                ? "sync failing"
                : "not connected";
        return (
          <li
            key={f.stream}
            className={cn(
              "inline-flex h-7 items-center gap-2 rounded-full border px-3 text-[11px] font-semibold whitespace-nowrap",
              tone === "success" && "border-success/25 bg-success/8 text-success",
              tone === "warning" && "border-warning/30 bg-warning/10 text-warning",
              tone === "danger" && "border-destructive/30 bg-destructive/8 text-destructive",
              tone === "neutral" && "border-border bg-muted text-foreground/55",
            )}
          >
            <Dot tone={tone} />
            {STREAM_LABEL[f.stream]} · {text}
          </li>
        );
      })}
    </ul>
  );
}

function BriefingView({ b }: { b: Briefing }) {
  const { sales, labor, staffing, stock } = b;
  return (
    <>
      <FreshnessStrip b={b} />

      {b.limitations.length > 0 && (
        <div
          role="status"
          className="flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning/10 px-3.5 py-3 text-[12px] leading-relaxed text-warning"
        >
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <ul className="min-w-0 space-y-1">
            {b.limitations.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </div>
      )}

      {b.highlights.length > 0 && (
        <ul className="space-y-1.5 text-[14px] leading-relaxed">
          {b.highlights.map((h, i) => (
            <li key={i} className="flex gap-2">
              <span aria-hidden className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
              <span>{h}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <Section title={`Sales · ${formatWeekday(b.results_date)} ${formatDate(b.results_date)}`} status={sales.status} source={sales.source} note={sales.note}>
          <Figure label="Net sales" value={formatMoney(sales.net_sales)} />
          <Figure label="Orders" value={formatInt(sales.orders)} />
          <Figure label="Average check" value={formatMoney(sales.avg_check, 2)} />
          {sales.compare_date && sales.net_sales_change_pct != null && (
            <Figure
              label={`vs ${formatWeekday(sales.compare_date)} ${formatDate(sales.compare_date)}`}
              value={`${sales.net_sales_change_pct >= 0 ? "+" : "−"}${Math.abs(sales.net_sales_change_pct).toFixed(1)}%`}
            />
          )}
        </Section>

        <Section title={`Labor · ${formatWeekday(b.results_date)} ${formatDate(b.results_date)}`} status={labor.status} source={labor.source} note={labor.note}>
          <Figure label="Labor cost" value={formatMoney(labor.actual_cost)} />
          <Figure label="Hours worked" value={labor.actual_hours == null ? "—" : formatHours(labor.actual_hours)} />
          <Figure label="Labor % of sales" value={formatPct(labor.labor_pct)} />
        </Section>

        <Section title="Staffing · published" status={staffing.status} source={staffing.source} note={staffing.note}>
          <Figure label="Shifts" value={formatInt(staffing.shifts)} />
          <Figure
            label="Scheduled hours"
            value={staffing.scheduled_hours == null ? "—" : formatHours(staffing.scheduled_hours)}
          />
          {!!staffing.unassigned_shifts && <Figure label="Open shifts" value={formatInt(staffing.unassigned_shifts)} />}
          {staffing.shift_list.length > 0 && (
            <ul className="col-span-2 mt-1 space-y-1 text-[12px] text-foreground/65">
              {staffing.shift_list.slice(0, SHIFTS_SHOWN).map((s, i) => (
                <li key={i} className="flex justify-between gap-3">
                  <span className="min-w-0 truncate">
                    {s.employee_name ?? <em className="text-warning">Open</em>} · {s.position_name}
                  </span>
                  <span className="shrink-0 tabular-nums">{formatShiftRange(s.starts_at, s.ends_at, b.timezone)}</span>
                </li>
              ))}
              {staffing.shift_list.length > SHIFTS_SHOWN && (
                <li className="text-foreground/45">+{staffing.shift_list.length - SHIFTS_SHOWN} more</li>
              )}
            </ul>
          )}
          {staffing.published_at && (
            <p className="col-span-2 text-[11px] text-foreground/45">
              Published {formatDateTime(staffing.published_at, b.timezone)}
            </p>
          )}
        </Section>

        <Section title="Below par · now" status={stock.status} source={stock.source} note={stock.note}>
          <Figure label="Items below par" value={formatInt(stock.below_par_count)} />
          {stock.items_without_par != null && stock.items_without_par > 0 && (
            <Figure label="Items without a par" value={formatInt(stock.items_without_par)} />
          )}
          {stock.items.length > 0 && (
            <ul className="col-span-2 mt-1 space-y-1 text-[12px] text-foreground/65">
              {stock.items.map((it) => (
                <li key={it.item_id} className="flex justify-between gap-3">
                  <span className="min-w-0 truncate">{it.name}</span>
                  <span className="shrink-0 tabular-nums">
                    {formatQty(it.on_hand)} / {formatQty(it.par)} {it.base_unit}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {stock.as_of && (
            <p className="col-span-2 text-[11px] text-foreground/45">
              Stock as of {formatDateTime(stock.as_of, b.timezone)}
            </p>
          )}
        </Section>
      </div>

      {b.evidence.length > 0 && (
        <details className="group text-[12px]">
          <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded font-medium text-foreground/55 hover:text-foreground/80 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
            <ChevronRight className="size-3 transition-transform group-open:rotate-90" aria-hidden />
            Figures behind this briefing
          </summary>
          <div className="mt-2 overflow-hidden rounded-xl border border-border">
            <table className="w-full text-[12px]">
              <caption className="sr-only">Figures behind this briefing</caption>
              <tbody className="divide-y divide-border">
                {b.evidence.map((e, i) => (
                  <tr key={i}>
                    <th scope="row" className="px-3 py-1.5 text-left font-normal text-foreground/65">
                      {e.label}
                    </th>
                    <td className="hidden px-3 py-1.5 font-mono text-[11px] text-foreground/40 sm:table-cell">{e.source}</td>
                    <td className="px-3 py-1.5 text-right font-semibold tabular-nums">{formatEvidence(e)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      <p className="text-[11px] text-foreground/45">
        Built {formatClock(b.generated_at, b.timezone)} from ChefOS data · no assistant needed
      </p>
    </>
  );
}

function formatQty(n: number): string {
  return Number.isInteger(n) ? formatInt(n) : n.toFixed(1);
}

const STATUS_BADGE: Record<BriefingSectionStatus, { text: string; tone: Tone } | null> = {
  ok: null,
  no_data: { text: "No data", tone: "neutral" },
  unavailable: { text: "Unavailable", tone: "danger" },
};

function Section({
  title,
  status,
  source,
  note,
  children,
}: {
  title: string;
  status: BriefingSectionStatus;
  source: string;
  note: string | null;
  children: ReactNode;
}) {
  const badge = STATUS_BADGE[status];
  return (
    <section
      className={cn(
        "rounded-xl border p-4",
        status === "unavailable" ? "border-destructive/30 bg-destructive/5" : "border-border",
      )}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="eyebrow">{title}</h3>
        {badge && <Badge tone={badge.tone}>{badge.text}</Badge>}
      </div>
      {status === "ok" ? (
        <>
          <div className="grid grid-cols-2 gap-x-3 gap-y-2">{children}</div>
          {note && <p className="mt-2 text-[11px] text-foreground/50">{note}</p>}
        </>
      ) : (
        <p className="text-[12px] leading-relaxed text-foreground/60">
          {note}
          {status === "unavailable" && (
            <span className="mt-1 block font-mono text-[11px] text-destructive">Source: {source}</span>
          )}
        </p>
      )}
    </section>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <dl>
      <dt className="text-[11px] text-foreground/50">{label}</dt>
      <dd className="text-[18px] font-semibold tracking-[-0.03em] tabular-nums">{value}</dd>
    </dl>
  );
}
