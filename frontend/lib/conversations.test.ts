// Run: node --test lib/*.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";

import type { ChatMessage } from "./chat-types.ts";
import { type Conversation, createStore, matchConversations, STORAGE_KEY, titleOf } from "./conversations.ts";
import { ANSWERED } from "./fixtures.ts";
import { ChatResult } from "./schemas.ts";

function memory() {
  const data = new Map<string, string>();
  return { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v), data };
}

const user = (text: string): ChatMessage => ({ id: `u-${text}`, role: "user", parts: [{ type: "text", text }] });
const reply = (): ChatMessage => ({
  id: "a1", role: "assistant",
  parts: [{ type: "data-result", data: ChatResult.parse(ANSWERED) }, { type: "text", text: ANSWERED.answer }],
});
const conv = (id: string, updatedAt: number, messages: ChatMessage[]): Conversation =>
  ({ id, title: titleOf(messages), threadId: `t-${id}`, updatedAt, messages });

test("a saved turn comes back whole (result, chart, thread id), newest first", () => {
  const kv = memory();
  const store = createStore(() => kv);
  store.save(conv("a", 1, [user("2024년 출생아 수"), reply()]));
  store.save(conv("b", 2, [user("혼인 건수")]));
  // a fresh store over the same storage = a page reload
  const again = createStore(() => kv);
  assert.deepEqual(again.list().map((c) => c.id), ["b", "a"]);
  const a = again.get("a")!;
  assert.equal(a.threadId, "t-a");
  const part = a.messages[1].parts[0];
  assert.ok(part.type === "data-result" && part.data.chart && part.data.stat_answer?.result.facts[0].value === 100);
});

test("saving again replaces the entry; remove deletes it; listeners hear both", () => {
  const kv = memory();
  const store = createStore(() => kv);
  let calls = 0;
  store.subscribe(() => calls++);
  store.save(conv("a", 1, [user("q1")]));
  store.save(conv("a", 3, [user("q1"), reply(), user("q2")]));
  assert.equal(store.list().length, 1);
  assert.equal(store.get("a")!.messages.length, 3);
  store.remove("a");
  assert.equal(store.get("a"), null);
  assert.equal(calls, 3);
});

test("the snapshot is stable until the storage changes (useSyncExternalStore)", () => {
  const kv = memory();
  const store = createStore(() => kv);
  store.save(conv("a", 1, [user("q")]));
  assert.equal(store.list(), store.list());
});

test("broken storage or no storage (server render) reads as empty", () => {
  const kv = memory();
  kv.setItem(STORAGE_KEY, "{not json");
  assert.deepEqual(createStore(() => kv).list(), []);
  assert.deepEqual(createStore(() => null).list(), []);
});

test("titles and search", () => {
  assert.equal(titleOf([]), "새 대화");
  assert.equal(titleOf([user("가".repeat(50))]).length, 41);
  const list = [conv("a", 1, [user("2024년 출생아 수"), reply()]), conv("b", 2, [user("혼인 건수")])];
  assert.deepEqual(matchConversations(list, "출생").map((c) => c.id), ["a"]);
  assert.deepEqual(matchConversations(list, "100명").map((c) => c.id), ["a"]); // the answer text counts
  assert.equal(matchConversations(list, "  ").length, 2);
});
