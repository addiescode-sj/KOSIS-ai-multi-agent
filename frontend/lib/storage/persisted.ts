import type { z } from "zod";

// The one place the app touches sessionStorage / localStorage (T3-55). A persisted value is a small external store:
// read through a zod schema (a broken or old entry reads as the fallback, it never takes the page down), written as
// JSON, and shared by every reader of the same key (one store per area + key). Storage that throws (private mode,
// quota, the server render) is caught: the value then lives in memory for the life of the page.
//
// Versions: the key ends in ".v<n>" (kosis.conversations.v1). A new shape gets a new number; the entries of older
// numbers under the same name are removed on first read.

export type Area = "session" | "local";
export type KV = Pick<Storage, "getItem" | "setItem"> & Partial<Pick<Storage, "removeItem">>;

export type PersistedStore<T> = {
  readonly fallback: T; // the server render's snapshot
  get(): T; // the same value until the entry changes (useSyncExternalStore needs a stable snapshot)
  set(next: T | ((prev: T) => T)): void;
  reset(): void; // remove the entry: reads the fallback again
  refresh(): void; // the entry changed outside this store (another tab): readers read again
  subscribe(listener: () => void): () => void;
};

export type PersistedOptions<T> = {
  storage: () => KV | null; // null: no storage (server render)
  fallback: T;
  // one-time move of an entry kept elsewhere before (T3-55: guest list from localStorage into this tab's session)
  legacy?: () => KV | null;
};

/** The browser storage of an area, or null on the server or when the browser refuses access. */
export function browserStorage(area: Area): KV | null {
  if (typeof window === "undefined") return null;
  try {
    return area === "session" ? window.sessionStorage : window.localStorage;
  } catch {
    return null; // e.g. storage disabled for the site
  }
}

/** "kosis.conversations.v2" -> the keys of versions before it ("kosis.conversations.v1"); none without ".v<n>". */
export function olderKeys(key: string): string[] {
  const m = /^(.*)\.v(\d+)$/.exec(key);
  if (!m) return [];
  return Array.from({ length: Number(m[2]) - 1 }, (_, i) => `${m[1]}.v${i + 1}`);
}

/** Move `key` from `from` to `to` once: copied when `to` has none, then removed from `from`. Never throws. */
export function migrateEntry(key: string, from: KV | null, to: KV | null): boolean {
  if (!from || !to || from === to) return false;
  try {
    const old = from.getItem(key);
    if (old === null) return false;
    if (to.getItem(key) === null) to.setItem(key, old);
    from.removeItem?.(key);
    return true;
  } catch {
    return false;
  }
}

/** Parse a stored JSON string with the schema; the fallback when absent, broken or of another shape. */
export function decode<T>(raw: string | null, schema: z.ZodType<T>, fallback: T): T {
  if (raw === null) return fallback;
  try {
    const parsed = schema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : fallback;
  } catch {
    return fallback;
  }
}

export function createPersisted<T>(key: string, schema: z.ZodType<T>, options: PersistedOptions<T>): PersistedStore<T> {
  const { storage, fallback, legacy } = options;
  const listeners = new Set<() => void>();
  let prepared = false;
  let cacheRaw: string | null | undefined;
  let cache: T = fallback;
  let memory: string | null = null; // used when the storage cannot be written
  let useMemory = false;

  function prepare(kv: KV) {
    if (prepared) return;
    prepared = true;
    if (legacy) migrateEntry(key, legacy(), kv);
    for (const old of olderKeys(key)) {
      try { kv.removeItem?.(old); } catch { /* best effort */ }
    }
  }

  function raw(): string | null {
    if (useMemory) return memory;
    const kv = storage();
    if (!kv) return null;
    try {
      prepare(kv);
      return kv.getItem(key);
    } catch {
      return null;
    }
  }

  function get(): T {
    const r = raw();
    if (r !== cacheRaw) {
      cacheRaw = r;
      cache = decode(r, schema, fallback);
    }
    return cache;
  }

  function write(value: string | null) {
    const kv = useMemory ? null : storage();
    try {
      if (!kv) throw new Error("no storage");
      if (value === null) kv.removeItem?.(key);
      else kv.setItem(key, value);
    } catch {
      if (typeof window !== "undefined") useMemory = true; // keep working on this page (quota, private mode)
      memory = value;
    }
    listeners.forEach((l) => l());
  }

  return {
    fallback,
    get,
    set(next) {
      const value = typeof next === "function" ? (next as (prev: T) => T)(get()) : next;
      write(JSON.stringify(value));
    },
    reset: () => write(null),
    refresh: () => listeners.forEach((l) => l()),
    subscribe(listener) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}

// One store per area + key for the whole tab, so every hook and module reading a key sees the same value.
const registry = new Map<string, PersistedStore<unknown>>();

export function persisted<T>(
  key: string, schema: z.ZodType<T>, opts: { area: Area; fallback: T; legacyArea?: Area },
): PersistedStore<T> {
  const id = `${opts.area}:${key}`;
  let store = registry.get(id) as PersistedStore<T> | undefined;
  if (!store) {
    const legacyArea = opts.legacyArea;
    store = createPersisted(key, schema, {
      storage: () => browserStorage(opts.area),
      fallback: opts.fallback,
      legacy: legacyArea ? () => browserStorage(legacyArea) : undefined,
    });
    registry.set(id, store as PersistedStore<unknown>);
    // another tab changed a localStorage entry: tell this tab's readers (sessionStorage is per tab)
    if (opts.area === "local" && typeof window !== "undefined") {
      const s = store;
      window.addEventListener("storage", (e) => { if (e.key === key) s.refresh(); });
    }
  }
  return store;
}
