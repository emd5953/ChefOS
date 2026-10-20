import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import type { EmployeePosition, LoginStatus, Position } from "@/lib/types";

/** "18.5" / "$18.50" -> 1850 cents; "" -> null; NaN for anything else. */
export function parseDollars(value: string): number | null {
  const v = value.replace(/[$,\s]/g, "");
  if (!v) return null;
  if (!/^\d+(\.\d{1,2})?$/.test(v)) return Number.NaN;
  return Math.round(Number(v) * 100);
}

export function centsToInput(cents: number | null | undefined): string {
  return cents == null ? "" : (cents / 100).toFixed(2);
}

export function PositionChip({
  position,
  className,
}: {
  position: Pick<EmployeePosition | Position, "name" | "color"> & { is_primary?: boolean };
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap",
        className,
      )}
    >
      <span aria-hidden className="size-2 rounded-full" style={{ background: position.color }} />
      {position.name}
      {position.is_primary && <span className="sr-only"> (primary)</span>}
    </span>
  );
}

const LOGIN: Record<LoginStatus, { label: string; tone: "neutral" | "accent" | "success" }> = {
  none: { label: "No login", tone: "neutral" },
  invited: { label: "Invited", tone: "accent" },
  active: { label: "Active", tone: "success" },
};

export function LoginBadge({ status }: { status: LoginStatus }) {
  const l = LOGIN[status];
  return <Badge tone={l.tone}>{l.label}</Badge>;
}

export function Tabs<T extends string>({
  value,
  onChange,
  tabs,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  tabs: Array<{ id: T; label: string; count?: number }>;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex rounded-full border border-border p-1">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            "h-8 rounded-full px-4 text-[13px] font-semibold tracking-[-0.01em] transition-[color,background-color,transform] duration-150 active:scale-[0.97]",
            value === t.id ? "bg-foreground text-background" : "text-foreground/55 hover:text-foreground",
          )}
        >
          {t.label}
          {t.count != null && t.count > 0 && <span className="ml-1.5 tabular opacity-70">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  disabled,
  children,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <label className={cn("flex min-h-10 items-center gap-3 rounded-lg px-2 text-[13px]", disabled ? "opacity-50" : "cursor-pointer hover:bg-muted")}>
      <input
        type="checkbox"
        className="size-4 accent-[var(--accent)]"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {children}
    </label>
  );
}
