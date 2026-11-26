import { expect, test } from "@jest/globals";

import { MemberChat, MemberChatList } from "../auth/schemas.ts";
import { resultFrom } from "../backend/request.ts";
import { chatFrom, chatPageFrom, nextCursor } from "./read.ts";

const chat = { id: "c1", title: "출생아 수", turns: [{ turn: 1, question: "출생아 수", result: {}, created_at: "2026-11-24T01:00:00Z", feedback: null }] };
const one = (status: number, body: unknown) => chatFrom(resultFrom(status, body === null ? "" : JSON.stringify(body), MemberChat));
const page = (status: number, body: unknown) => chatPageFrom(resultFrom(status, body === null ? "" : JSON.stringify(body), MemberChatList));

test("a saved conversation is ready; 404 is its own 'missing' screen", () => {
  expect(one(200, chat)).toEqual({ status: "ready", chat });
  expect(one(404, { error: { code: "not_found", message: "없음" } })).toEqual({ status: "missing" });
});

test("a server error, a malformed body or no answer is an error value, never a throw", () => {
  expect(one(500, null)).toMatchObject({ status: "error", message: expect.stringContaining("500") });
  expect(one(200, { id: "c1" })).toEqual({ status: "error", message: "대화 형식이 다릅니다" });
  expect(chatFrom({ ok: false, status: null, code: "network", message: "x" }).status).toBe("error");
});

test("MemberChat: a turn's feedback is {value, category} or null; anything else is rejected", () => {
  const turn = chat.turns[0];
  expect(MemberChat.safeParse({ ...chat, turns: [{ ...turn, feedback: { value: "down", category: "wrong_number" } }] }).success).toBe(true);
  expect(MemberChat.safeParse({ ...chat, turns: [{ ...turn, feedback: { value: "meh", category: null } }] }).success).toBe(false);
  expect(MemberChat.safeParse({ ...chat, turns: [{ ...turn, turn: "1" }] }).success).toBe(false);
});

test("a list page is a value; a failed page asks for the same cursor again", () => {
  const items = [{ id: "c1", title: "t", created_at: "2026-11-24T01:00:00Z", updated_at: "2026-11-24T01:00:00Z", questions: 1 }];
  expect(page(200, { items, next_cursor: "n2" })).toEqual({ ok: true, items, next_cursor: "n2" });
  expect(page(200, { items, next_cursor: null })).toMatchObject({ next_cursor: null });
  expect(page(200, { items: [{ id: "c1" }], next_cursor: null })).toEqual({ ok: false, message: "대화 목록 형식이 다릅니다" });
  expect(page(503, null)).toMatchObject({ ok: false, message: expect.stringContaining("503") });
  expect(chatPageFrom({ ok: false, status: null, code: "network", message: "x" }).ok).toBe(false);
  expect(nextCursor({ ok: true, items, next_cursor: "n2" }, null)).toBe("n2");
  expect(nextCursor({ ok: false, message: "x" }, "n2")).toBe("n2");
  expect(nextCursor({ ok: false, message: "x" }, null)).toBeNull();
});
