"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";
import { compareRange, isPreset, presetRange, todayIn, type PresetId } from "./dates";
import type { CompareMode, DateRange } from "./types";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Date range + compare mode, stored in URL search params so views are shareable. */
export function useDateRange(timeZone: string | undefined, defaultPreset: PresetId = "last_30") {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();

  const today = useMemo(() => todayIn(timeZone), [timeZone]);
  const rawPreset = search.get("range");
  const preset: PresetId = isPreset(rawPreset) ? rawPreset : defaultPreset;
  const from = search.get("from");
  const to = search.get("to");
  const compare: CompareMode = search.get("compare") === "previous_year" ? "previous_year" : "previous_period";

  const range: DateRange = useMemo(() => {
    if (preset === "custom") {
      const f = from && DATE_RE.test(from) ? from : presetRange("last_30", today).from;
      const t = to && DATE_RE.test(to) ? to : today;
      return f <= t ? { from: f, to: t } : { from: t, to: f };
    }
    return presetRange(preset, today);
  }, [preset, from, to, today]);

  const compareWindow = useMemo(() => compareRange(range, compare), [range, compare]);

  const update = useCallback(
    (patch: Record<string, string | null>) => {
      const sp = new URLSearchParams(search.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v == null) sp.delete(k);
        else sp.set(k, v);
      }
      router.replace(`${pathname}?${sp.toString()}`, { scroll: false });
    },
    [router, pathname, search],
  );

  const setPreset = useCallback(
    (id: PresetId) => {
      if (id === "custom") update({ range: id, from: range.from, to: range.to });
      else update({ range: id, from: null, to: null });
    },
    [update, range],
  );
  const setCustom = useCallback(
    (next: Partial<DateRange>) => {
      let f = next.from ?? range.from;
      let t = next.to ?? range.to;
      if (f > t) {
        if (next.from) t = f;
        else f = t;
      }
      update({ range: "custom", from: f, to: t });
    },
    [update, range],
  );
  const setCompare = useCallback(
    (mode: CompareMode) => update({ compare: mode === "previous_period" ? null : mode }),
    [update],
  );

  return { today, preset, range, compare, compareWindow, setPreset, setCustom, setCompare };
}
