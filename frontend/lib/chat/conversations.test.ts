import { expect, test } from "@jest/globals";

import type { ChatMessage } from "../../types/chat.ts";
import type { Conversation } from "../../types/conversation.ts";
import { createStore, matchConversations, STORAGE_KEY, titleOf } from "./conversations.ts";
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
  expect(again.list().map((c) => c.id)).toEqual(["b", "a"]);
  const a = again.get("a")!;
  expect(a.threadId).toBe("t-a");
  const part = a.messages[1].parts[0];
  expect(part.type === "data-result" && part.data.chart && part.data.stat_answer?.result.facts[0].value === 100).toBe(true);
});

test("saving again replaces the entry; remove deletes it; listeners hear both", () => {
  const kv = memory();
  const store = createStore(() => kv);
  let calls = 0;
  store.subscribe(() => calls++);
  store.save(conv("a", 1, [user("q1")]));
  store.save(conv("a", 3, [user("q1"), reply(), user("q2")]));
  expect(store.list()).toHaveLength(1);
  expect(store.get("a")!.messages).toHaveLength(3);
  store.remove("a");
  expect(store.get("a")).toBeNull();
  expect(calls).toBe(3);
});

test("the snapshot is stable until the storage changes (useSyncExternalStore)", () => {
  const kv = memory();
  const store = createStore(() => kv);
  store.save(conv("a", 1, [user("q")]));
  expect(store.list()).toBe(store.list());
});

test("broken storage or no storage (server render) reads as empty", () => {
  const kv = memory();
  kv.setItem(STORAGE_KEY, "{not json");
  expect(createStore(() => kv).list()).toEqual([]);
  expect(createStore(() => null).list()).toEqual([]);
});

test("titles and search", () => {
  expect(titleOf([])).toBe("새 대화");
  expect(titleOf([user("가".repeat(50))])).toHaveLength(41);
  const list = [conv("a", 1, [user("2024년 출생아 수"), reply()]), conv("b", 2, [user("혼인 건수")])];
  expect(matchConversations(list, "출생").map((c) => c.id)).toEqual(["a"]);
  expect(matchConversations(list, "100명").map((c) => c.id)).toEqual(["a"]); // the answer text counts
  expect(matchConversations(list, "  ")).toHaveLength(2);
});
