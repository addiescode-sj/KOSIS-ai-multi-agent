import { expect, test } from "@jest/globals";
import { z } from "zod";

import { createPersisted, decode, migrateEntry, olderKeys } from "./persisted.ts";

function memory(init: Record<string, string> = {}) {
  const data = new Map<string, string>(Object.entries(init));
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    data,
  };
}

const Count = z.object({ n: z.number() });
const ZERO = { n: 0 };

test("decode: absent, broken JSON and another shape all read as the fallback", () => {
  expect(decode(null, Count, ZERO)).toBe(ZERO);
  expect(decode("{not json", Count, ZERO)).toBe(ZERO);
  expect(decode('{"n":"x"}', Count, ZERO)).toBe(ZERO);
  expect(decode('{"n":2}', Count, ZERO)).toEqual({ n: 2 });
});

test("a value survives a reload (a new store over the same storage); reset reads the fallback", () => {
  const kv = memory();
  const store = createPersisted("k.v1", Count, { storage: () => kv, fallback: ZERO });
  store.set({ n: 1 });
  store.set((prev) => ({ n: prev.n + 1 }));
  expect(createPersisted("k.v1", Count, { storage: () => kv, fallback: ZERO }).get()).toEqual({ n: 2 });
  store.reset();
  expect(kv.data.has("k.v1")).toBe(false);
  expect(store.get()).toBe(ZERO);
});

test("the snapshot is the same object until the entry changes; listeners hear set and reset", () => {
  const kv = memory({ "k.v1": '{"n":5}' });
  const store = createPersisted("k.v1", Count, { storage: () => kv, fallback: ZERO });
  let calls = 0;
  const off = store.subscribe(() => calls++);
  expect(store.get()).toBe(store.get());
  store.set({ n: 6 });
  store.reset();
  off();
  store.set({ n: 7 });
  expect(calls).toBe(2);
});

test("no storage (server render) or storage that throws reads the fallback, never throws", () => {
  expect(createPersisted("k.v1", Count, { storage: () => null, fallback: ZERO }).get()).toBe(ZERO);
  const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("quota"); } };
  const store = createPersisted("k.v1", Count, { storage: () => broken, fallback: ZERO });
  expect(store.get()).toBe(ZERO);
  expect(() => store.set({ n: 1 })).not.toThrow();
});

test("a new version removes the entries of older versions on first read", () => {
  expect(olderKeys("kosis.conversations.v3")).toEqual(["kosis.conversations.v1", "kosis.conversations.v2"]);
  expect(olderKeys("no-version")).toEqual([]);
  const kv = memory({ "k.v1": '{"old":true}', "k.v2": '{"n":3}' });
  expect(createPersisted("k.v2", Count, { storage: () => kv, fallback: ZERO }).get()).toEqual({ n: 3 });
  expect(kv.data.has("k.v1")).toBe(false);
});

test("migration: an entry kept in the legacy storage moves once, and is removed there", () => {
  const local = memory({ "k.v1": '{"n":9}' });
  const session = memory();
  const store = createPersisted("k.v1", Count, { storage: () => session, fallback: ZERO, legacy: () => local });
  expect(store.get()).toEqual({ n: 9 });
  expect(session.data.get("k.v1")).toBe('{"n":9}');
  expect(local.data.has("k.v1")).toBe(false);
});

test("migration keeps what this tab already has; nothing to move is a no-op", () => {
  const local = memory({ k: "old" });
  const session = memory({ k: "mine" });
  expect(migrateEntry("k", local, session)).toBe(true);
  expect(session.data.get("k")).toBe("mine");
  expect(local.data.has("k")).toBe(false);
  expect(migrateEntry("k", local, session)).toBe(false);
  expect(migrateEntry("k", null, session)).toBe(false);
});
