import { expect, test } from "@jest/globals";

import { EMPTY_MESSAGE, EmptyReason, ListBody, emptyReasonOf, listBodyOf, showsLoginPrompt } from "./list-view.ts";

const base = { error: null, loading: false, items: [], more: false, member: false };
const item = { id: "c1", title: "t", updatedAt: 0, questions: 1 };

test("list body: error first, then loading, then rows or empty", () => {
  expect(listBodyOf({ ...base, error: "x", loading: true }, 3)).toBe(ListBody.ERROR);
  expect(listBodyOf({ ...base, loading: true }, 0)).toBe(ListBody.LOADING);
  expect(listBodyOf(base, 2)).toBe(ListBody.ITEMS);
  expect(listBodyOf(base, 0)).toBe(ListBody.EMPTY);
});

test("empty reason: no chats at all, no match so far (more pages), no match", () => {
  expect(emptyReasonOf(base)).toBe(EmptyReason.NO_CHATS);
  expect(emptyReasonOf({ ...base, items: [item], more: true })).toBe(EmptyReason.NO_MATCH_SO_FAR);
  expect(emptyReasonOf({ ...base, items: [item] })).toBe(EmptyReason.NO_MATCH);
  for (const reason of Object.values(EmptyReason)) expect(EMPTY_MESSAGE[reason]).toBeTruthy();
});

test("login prompt: guests only, once the list is there", () => {
  expect(showsLoginPrompt(base)).toBe(true);
  expect(showsLoginPrompt({ ...base, loading: true })).toBe(false);
  expect(showsLoginPrompt({ ...base, member: true })).toBe(false);
});
