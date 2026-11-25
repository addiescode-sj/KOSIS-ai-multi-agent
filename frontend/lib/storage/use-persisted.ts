"use client";

import { useCallback, useSyncExternalStore } from "react";
import type { z } from "zod";

import { persisted, type Area } from "./persisted";

export type UsePersistedOptions<T> = {
  area: Area; // "session": this tab only (guest conversations, api.md 1장) · "local": this browser (notification switches)
  fallback?: T; // read when absent, broken or of another shape; default: what the schema gives for undefined
  legacyArea?: Area; // move an entry kept there before into `area` once (T3-55 migration)
};

/**
 * A value kept in this browser, as React state: [value, set]. The one persistence hook of the app; modules that
 * need the value outside React read the same store through `persisted()`. The server render (no storage) sees
 * the fallback, so a page renders the same on the server and on the first client pass.
 */
export function usePersisted<S extends z.ZodType>(
  key: string, schema: S, options: UsePersistedOptions<z.output<S>>,
): [z.output<S>, (next: z.output<S> | ((prev: z.output<S>) => z.output<S>)) => void] {
  const fallback = options.fallback ?? (schema.safeParse(undefined).data as z.output<S>);
  const store = persisted<z.output<S>>(key, schema as z.ZodType<z.output<S>>, {
    area: options.area, fallback, legacyArea: options.legacyArea,
  });
  const value = useSyncExternalStore(store.subscribe, store.get, () => store.fallback);
  const set = useCallback((next: z.output<S> | ((prev: z.output<S>) => z.output<S>)) => store.set(next), [store]);
  return [value, set];
}
