"use client";

import { Star } from "lucide-react";
import { Field, Input } from "@/components/ui/field";
import { cn } from "@/lib/cn";
import type { Location, Position, PositionRef } from "@/lib/types";
import { Checkbox } from "./shared";

/** Positions picker: check any number, star one as primary. */
export function PositionsPicker({
  positions,
  value,
  onChange,
  error,
}: {
  positions: Position[];
  value: PositionRef[];
  onChange: (v: PositionRef[]) => void;
  error?: string;
}) {
  const selected = new Map(value.map((r) => [r.position_id, !!r.is_primary]));
  const toggle = (id: string, on: boolean) => {
    if (on) {
      const next = [...value, { position_id: id, is_primary: value.length === 0 }];
      onChange(next);
    } else {
      const rest = value.filter((r) => r.position_id !== id);
      if (rest.length && !rest.some((r) => r.is_primary)) rest[0] = { ...rest[0]!, is_primary: true };
      onChange(rest);
    }
  };
  const makePrimary = (id: string) => onChange(value.map((r) => ({ ...r, is_primary: r.position_id === id })));

  return (
    <fieldset className="space-y-1.5">
      <legend className="mb-1.5 block text-[12px] font-semibold text-foreground/80">Positions</legend>
      {positions.length === 0 ? (
        <p className="text-xs text-foreground/45">No positions yet. Add them on the Positions tab.</p>
      ) : (
        <div className="grid gap-0.5 sm:grid-cols-2">
          {positions.map((p) => {
            const on = selected.has(p.id);
            const primary = selected.get(p.id) === true;
            return (
              <Checkbox key={p.id} checked={on} onChange={(c) => toggle(p.id, c)} label={p.name}>
                <span aria-hidden className="size-2 rounded-full" style={{ background: p.color }} />
                {on && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.preventDefault();
                      makePrimary(p.id);
                    }}
                    aria-pressed={primary}
                    aria-label={primary ? `${p.name} is primary` : `Make ${p.name} primary`}
                    className={cn(
                      "grid size-7 place-items-center rounded-full transition-[color,transform] duration-150 active:scale-[0.88]",
                      primary ? "text-warning" : "text-foreground/30 hover:text-foreground/60",
                    )}
                  >
                    <Star className={cn("size-3.5", primary && "fill-current")} aria-hidden />
                  </button>
                )}
              </Checkbox>
            );
          })}
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </fieldset>
  );
}

export function LocationsPicker({
  locations,
  value,
  onChange,
  error,
}: {
  locations: Location[];
  value: string[];
  onChange: (v: string[]) => void;
  error?: string;
}) {
  return (
    <fieldset className="space-y-1.5">
      <legend className="mb-1.5 block text-[12px] font-semibold text-foreground/80">Works at</legend>
      <div className="grid gap-0.5 sm:grid-cols-2">
        {locations.map((l) => (
          <Checkbox
            key={l.id}
            label={l.name}
            checked={value.includes(l.id)}
            onChange={(c) => onChange(c ? [...value, l.id] : value.filter((id) => id !== l.id))}
          />
        ))}
      </div>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </fieldset>
  );
}

export function RateField({
  value,
  onChange,
  error,
  hint,
  label = "Rate override ($/h)",
}: {
  value: string;
  onChange: (v: string) => void;
  error?: string;
  hint?: string;
  label?: string;
}) {
  return (
    <Field label={label} error={error} hint={hint}>
      {(p) => (
        <Input {...p} inputMode="decimal" placeholder="18.50" value={value} onChange={(e) => onChange(e.target.value)} />
      )}
    </Field>
  );
}
