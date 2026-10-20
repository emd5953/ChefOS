"use client";

import { useSyncExternalStore } from "react";
import { browserTimeZone } from "./dates";

const noop = () => () => {};

/** The browser's IANA zone; null during SSR and hydration so markup matches. */
export function useBrowserTimeZone(): string | null {
  return useSyncExternalStore(noop, browserTimeZone, () => null);
}
