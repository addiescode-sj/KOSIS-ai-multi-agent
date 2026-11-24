import { expect, test } from "@jest/globals";

import { chatFrom } from "./read.ts";

const chat = { id: "c1", title: "출생아 수", turns: [{ turn: 1, question: "출생아 수", result: {}, created_at: "2026-11-24T01:00:00Z" }] };

test("a saved conversation is ready; 404 is its own 'missing' screen", () => {
  expect(chatFrom({ status: 200, body: chat })).toEqual({ status: "ready", chat });
  expect(chatFrom({ status: 404, body: { detail: "not found" } })).toEqual({ status: "missing" });
});

test("a server error, a malformed body or no answer is an error value, never a throw", () => {
  expect(chatFrom({ status: 500, body: null })).toMatchObject({ status: "error", message: expect.stringContaining("500") });
  expect(chatFrom({ status: 200, body: { id: "c1" } }).status).toBe("error");
  expect(chatFrom(null).status).toBe("error");
});
