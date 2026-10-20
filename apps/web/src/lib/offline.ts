// Offline copy of the employee's own published week (spec: MOB-1). Only /v1/me/week
// responses are kept: the employee's own data, never rates. Cleared on logout.

import type { MyWeek } from "./types";

const PREFIX = "chefos:my-week:";

export type CachedWeek = { savedAt: string; data: MyWeek };

function key(userId: string, week: string): string {
  return `${PREFIX}${userId}:${week}`;
}

export function saveMyWeek(userId: string, week: string, data: MyWeek): void {
  try {
    window.localStorage.setItem(key(userId, week), JSON.stringify({ savedAt: new Date().toISOString(), data }));
  } catch {
    // Storage full or disabled: offline viewing just won't be available.
  }
}

export function loadMyWeek(userId: string, week: string): CachedWeek | null {
  try {
    const raw = window.localStorage.getItem(key(userId, week));
    return raw ? (JSON.parse(raw) as CachedWeek) : null;
  } catch {
    return null;
  }
}

export function clearMyWeekCache(): void {
  try {
    for (let i = window.localStorage.length - 1; i >= 0; i--) {
      const k = window.localStorage.key(i);
      if (k?.startsWith(PREFIX)) window.localStorage.removeItem(k);
    }
  } catch {
    // Nothing cached, or storage unavailable.
  }
}
