import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Change chip. `kind="pct"` for relative change; `kind="pts"` for percentage
 * points. `invert` marks metrics where an increase is bad (labor %): the arrow
 * still points the real direction, but the colour is red for an increase.
 */
export function DeltaChip({
  value,
  kind = "pct",
  invert = false,
  label,
}: {
  value: number | null | undefined;
  kind?: "pct" | "pts";
  invert?: boolean;
  label?: string;
}) {
  if (value == null || Number.isNaN(value)) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-foreground/[0.06] px-2 py-1 text-[11px] font-semibold text-foreground/45">
        <Minus className="size-3" aria-hidden /> n/a
      </span>
    );
  }
  const rounded = Math.round(value * 10) / 10;
  const flat = rounded === 0;
  const up = rounded > 0;
  const good = flat ? null : invert ? !up : up;
  const Icon = flat ? Minus : up ? ArrowUpRight : ArrowDownRight;
  const text = `${up ? "+" : rounded < 0 ? "−" : ""}${Math.abs(rounded).toFixed(1)}${kind === "pct" ? "%" : " pts"}`;
  const sr = `${flat ? "No change" : up ? "Up" : "Down"} ${Math.abs(rounded).toFixed(1)} ${kind === "pct" ? "percent" : "percentage points"}${
    label ? ` ${label}` : ""
  }${good == null ? "" : good ? ", favorable" : ", unfavorable"}`;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 rounded-full px-2 py-1 text-[11px] font-semibold tabular",
        good == null && "bg-foreground/[0.06] text-foreground/55",
        good === true && "bg-success/10 text-success",
        good === false && "bg-destructive/10 text-destructive",
      )}
    >
      <Icon className="size-3" aria-hidden />
      <span aria-hidden>{text}</span>
      <span className="sr-only">{sr}</span>
    </span>
  );
}
