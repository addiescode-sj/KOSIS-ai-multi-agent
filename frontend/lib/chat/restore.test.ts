import { expect, test } from "@jest/globals";

import { MemberChat } from "../auth/schemas.ts";
import { textOf } from "./text.ts";
import { ANSWERED, REFUSED } from "./fixtures.ts";
import { conversationFrom, itemsFromServer, UNRESTORABLE } from "./restore.ts";

const chat = MemberChat.parse({
  id: "c1",
  title: "2024년 출생아 수",
  turns: [
    { turn: 2, question: "2030년은?", result: REFUSED, created_at: "2026-11-24T01:05:00Z" },
    { turn: 1, question: "2024년 출생아 수", result: ANSWERED, created_at: "2026-11-24T01:00:00Z" },
    { turn: 3, question: "옛 형식", result: { answer: "필드가 빠진 옛 기록" }, created_at: "2026-11-24T01:10:00Z" },
  ],
});

test("saved turns come back in turn order with the backend's result as it is; thread id = conversation id", () => {
  const c = conversationFrom(chat);
  expect(c.threadId).toBe("c1");
  expect(c.messages.filter((m) => m.role === "user").map(textOf)).toEqual(["2024년 출생아 수", "2030년은?", "옛 형식"]);
  const first = c.messages[1].parts.find((p) => p.type === "data-result");
  expect(first?.type === "data-result" && first.data.answer).toBe(ANSWERED.answer);
  expect(c.updatedAt).toBe(Date.parse("2026-11-24T01:10:00Z"));
});

test("a result that no longer parses is not redrawn from guesses: a fixed notice only", () => {
  const last = conversationFrom(chat).messages[5];
  expect(last.parts).toEqual([{ type: "data-notice", data: { text: UNRESTORABLE } }]);
});

test("the server list carries its question count", () => {
  expect(itemsFromServer([{ id: "c1", title: "t", updated_at: "2026-11-24T01:00:00Z", questions: 2 }])).toEqual([
    { id: "c1", title: "t", updatedAt: Date.parse("2026-11-24T01:00:00Z"), questions: 2 },
  ]);
});
