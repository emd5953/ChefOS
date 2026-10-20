"use client";

import { ChevronRight, TriangleAlert } from "lucide-react";
import { Badge, type Tone } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { formatDate, formatHours, formatInt, formatMoney, formatPct, formatRelative } from "@/lib/format";
import type { SousAnswer, SousEvidence, SousKind } from "@/lib/types";
import { useNow } from "@/lib/use-now";

export function formatEvidence(e: Pick<SousEvidence, "value" | "unit">): string {
  switch (e.unit) {
    case "usd_cents":
      return formatMoney(e.value);
    case "percent":
      return formatPct(e.value);
    case "percent_pts":
      return `${e.value < 0 ? "−" : "+"}${Math.abs(e.value).toFixed(1)} pts`;
    case "hours":
      return formatHours(e.value);
    default:
      return formatInt(e.value);
  }
}

const KIND_LABEL: Partial<Record<SousKind, { text: string; tone: Tone }>> = {
  clarify: { text: "Needs detail", tone: "accent" },
  refuse: { text: "Can’t help with that", tone: "neutral" },
  unsupported: { text: "Not supported yet", tone: "neutral" },
  evidence_only: { text: "Showing the numbers only", tone: "warning" },
};

export function AnswerView({ answer }: { answer: SousAnswer }) {
  const now = useNow();
  const label = KIND_LABEL[answer.kind];
  const muted = answer.kind === "refuse" || answer.kind === "unsupported";
  const stamps = answer.evidence.map((e) => e.as_of).filter((s): s is string => !!s).sort();
  const asOf = stamps[0];

  return (
    <div className="min-w-0 space-y-3">
      {label && <Badge tone={label.tone}>{label.text}</Badge>}
      {answer.mode === "fallback" && (
        <p className="text-[11px] font-medium text-foreground/45">Quick answer · assistant offline</p>
      )}
      {answer.kind === "evidence_only" && (
        <p className="text-[12px] leading-relaxed text-foreground/55">
          I couldn’t verify my own wording against the data, so here are the figures themselves.
        </p>
      )}
      <p
        className={cn(
          "whitespace-pre-wrap text-[14px] leading-relaxed",
          muted && "text-foreground/55",
          answer.kind === "clarify" && "font-medium",
        )}
      >
        {answer.answer}
      </p>

      {answer.limitations.length > 0 && (
        <div className="flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning/10 px-3.5 py-3 text-[12px] leading-relaxed text-warning">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <ul className="min-w-0 space-y-1">
            {answer.limitations.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </div>
      )}

      {answer.evidence.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-border">
          <table className="w-full text-[13px]">
            <caption className="sr-only">Figures behind this answer</caption>
            <tbody className="divide-y divide-border">
              {answer.evidence.map((e, i) => (
                <tr key={i}>
                  <th scope="row" className="px-3.5 py-2 text-left font-normal text-foreground/65">
                    {e.label}
                  </th>
                  <td className="px-3.5 py-2 text-right font-semibold tabular-nums">{formatEvidence(e)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(answer.period || asOf) && (
        <p className="text-[11px] text-foreground/45">
          {answer.period &&
            `${formatDate(answer.period.from)} – ${formatDate(answer.period.to)}, ${answer.period.timezone}`}
          {answer.period && asOf && " · "}
          {asOf && `Data as of ${formatRelative(asOf, now)}`}
        </p>
      )}

      <details className="group text-[11px] text-foreground/45">
        <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded font-medium hover:text-foreground/70 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
          <ChevronRight className="size-3 transition-transform group-open:rotate-90" aria-hidden />
          How I got this
        </summary>
        <div className="mt-2 space-y-1.5 font-mono text-[11px] leading-relaxed">
          {answer.tools_used.length === 0 ? (
            <p>No tools called.</p>
          ) : (
            <ul className="space-y-1">
              {answer.tools_used.map((t, i) => (
                <li key={i} className="break-all">
                  <span className={cn("font-semibold", !t.ok && "text-destructive")}>{t.name}</span>{" "}
                  {JSON.stringify(t.args)} · {t.ms} ms · {t.ok ? "ok" : "failed"}
                </li>
              ))}
            </ul>
          )}
          <p>
            {answer.model} · prompt {answer.prompt_version} · {answer.request_id}
          </p>
        </div>
      </details>
    </div>
  );
}
