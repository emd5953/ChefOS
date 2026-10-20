"use client";

import { CalendarDays } from "lucide-react";
import { useId } from "react";
import { Input, Select } from "@/components/ui/field";
import { cn } from "@/lib/cn";
import { PRESETS, type PresetId } from "@/lib/dates";
import { formatDate } from "@/lib/format";
import type { CompareMode, DateRange } from "@/lib/types";

export function RangeControls({
  preset,
  range,
  today,
  onPreset,
  onCustom,
  compare,
  compareWindow,
  onCompare,
}: {
  preset: PresetId;
  range: DateRange;
  today: string;
  onPreset: (p: PresetId) => void;
  onCustom: (next: Partial<DateRange>) => void;
  compare?: CompareMode;
  compareWindow?: DateRange;
  onCompare?: (m: CompareMode) => void;
}) {
  const id = useId();
  return (
    <div className="mb-6 flex flex-wrap items-end gap-x-6 gap-y-4">
      <div className="space-y-1.5">
        <label htmlFor={`${id}-range`} className="eyebrow block text-[10px]">
          Date range
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <div className="w-44">
            <Select
              id={`${id}-range`}
              value={preset}
              onChange={(e) => onPreset(e.target.value as PresetId)}
              className="h-10 rounded-full pl-4 text-[13px] font-medium"
            >
              {PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </Select>
          </div>
          {preset === "custom" && (
            <>
              <Input
                type="date"
                aria-label="Start date"
                value={range.from}
                max={today}
                onChange={(e) => e.target.value && onCustom({ from: e.target.value })}
                className="h-10 w-40 rounded-full text-[13px]"
              />
              <span aria-hidden className="text-foreground/35">
                &ndash;
              </span>
              <Input
                type="date"
                aria-label="End date"
                value={range.to}
                max={today}
                onChange={(e) => e.target.value && onCustom({ to: e.target.value })}
                className="h-10 w-40 rounded-full text-[13px]"
              />
            </>
          )}
        </div>
      </div>

      {compare && onCompare && (
        <div className="space-y-1.5">
          <span id={`${id}-cmp`} className="eyebrow block text-[10px]">
            Compare to
          </span>
          <div
            role="radiogroup"
            aria-labelledby={`${id}-cmp`}
            className="inline-flex rounded-full border border-border bg-muted p-0.5"
          >
            {(
              [
                ["previous_period", "Previous period"],
                ["previous_year", "Same period last year"],
              ] as const
            ).map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                role="radio"
                aria-checked={compare === mode}
                onClick={() => onCompare(mode)}
                className={cn(
                  "h-9 rounded-full px-3.5 text-[13px] font-medium transition-[color,background-color,box-shadow,transform] duration-150 active:scale-[0.97]",
                  compare === mode
                    ? "bg-background text-foreground shadow-sm"
                    : "text-foreground/55 hover:text-foreground",
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      <p className="flex items-center gap-2 pb-2.5 text-xs text-foreground/50">
        <CalendarDays className="size-3.5" aria-hidden />
        <span className="tabular">
          {formatDate(range.from, true)} &ndash; {formatDate(range.to, true)}
        </span>
        {compareWindow && (
          <span className="tabular text-foreground/35">
            vs {formatDate(compareWindow.from, true)} &ndash; {formatDate(compareWindow.to, true)}
          </span>
        )}
      </p>
    </div>
  );
}
