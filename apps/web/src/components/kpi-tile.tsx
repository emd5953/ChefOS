import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DeltaChip } from "@/components/ui/delta-chip";

export function KpiTile({
  label,
  value,
  delta,
  deltaKind = "pct",
  invert,
  note,
}: {
  label: string;
  value: string;
  delta: number | null | undefined;
  deltaKind?: "pct" | "pts";
  invert?: boolean;
  note?: string;
}) {
  return (
    <Card className="p-5">
      <p className="eyebrow">{label}</p>
      <p className="mt-3 text-[32px] font-semibold leading-none tracking-[-0.055em] tabular">{value}</p>
      <div className="mt-3 flex items-center gap-2">
        <DeltaChip value={delta} kind={deltaKind} invert={invert} label={`in ${label.toLowerCase()}`} />
        {note && <span className="text-xs text-foreground/45">{note}</span>}
      </div>
    </Card>
  );
}

export function KpiTileSkeleton() {
  return (
    <Card className="p-5" aria-hidden>
      <Skeleton className="h-3 w-20" />
      <Skeleton className="mt-4 h-8 w-32" />
      <Skeleton className="mt-4 h-5 w-24 rounded-full" />
    </Card>
  );
}
