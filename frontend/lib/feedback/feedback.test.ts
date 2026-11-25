import { expect, test } from "@jest/globals";

import { DOWN_CATEGORIES, feedbackBody, feedbackFrom, feedbackProblem, UP_CATEGORIES } from "./feedback.ts";
import { FeedbackRequest, FeedbackResponse } from "./schemas.ts";

const RID = "4bf92f3577b34da6a3ce929d0e0e4736";

test("the choices are api.md 3장's codes", () => {
  expect(UP_CATEGORIES).toEqual(["accurate_number", "right_table", "easy", "chart_helpful", "other"]);
  expect(DOWN_CATEGORIES).toEqual(["wrong_number", "wrong_table", "hard", "other"]);
});

test("the body: snake_case fields, reason trimmed and left out when empty, a null choice kept", () => {
  expect(feedbackBody({ requestId: RID, value: "down", category: "wrong_number", reason: "  숫자가 다릅니다 " }))
    .toEqual({ request_id: RID, value: "down", category: "wrong_number", reason: "숫자가 다릅니다" });
  expect(feedbackBody({ requestId: RID, value: "up", category: null, reason: "  " }))
    .toEqual({ request_id: RID, value: "up", category: null });
});

test("checked before sending: a choice of the other value, 'other' without a reason, a reason over 200", () => {
  expect(feedbackProblem({ requestId: RID, value: "up", category: "wrong_number" })).not.toBeNull();
  expect(feedbackProblem({ requestId: RID, value: "down", category: "other", reason: " " })).not.toBeNull();
  expect(feedbackProblem({ requestId: RID, value: "down", category: "hard", reason: "가".repeat(201) })).not.toBeNull();
  expect(feedbackProblem({ requestId: RID, value: "down", category: "other", reason: "가".repeat(200) })).toBeNull();
  expect(feedbackProblem({ requestId: RID, value: "up", category: null })).toBeNull();
});

test("FeedbackRequest schema: accepts the contract's combinations, rejects the rest", () => {
  const ok = [
    { request_id: RID, value: "up", category: "accurate_number" },
    { request_id: RID, value: "down", category: "other", reason: "다릅니다" },
    { request_id: RID, value: "up", category: null },
  ];
  for (const body of ok) expect(FeedbackRequest.safeParse(body).success).toBe(true);
  const bad = [
    { request_id: RID, value: "up", category: "wrong_number" }, // a choice of the other value
    { request_id: RID, value: "down", category: "other" }, // "other" without a reason
    { request_id: RID, value: "down", category: "hard", reason: "가".repeat(201) },
    { request_id: RID, value: "meh", category: null },
    { request_id: RID, value: "up", category: "accurate" }, // the old T3-48 code
    { request_id: RID, value: "up", category: null, extra: 1 },
    { value: "up", category: null },
  ];
  for (const body of bad) expect(FeedbackRequest.safeParse(body).success).toBe(false);
});

test("FeedbackResponse schema", () => {
  expect(FeedbackResponse.safeParse({ feedback_id: "f1", updated: false }).success).toBe(true);
  expect(FeedbackResponse.safeParse({ updated: false }).success).toBe(false);
  expect(FeedbackResponse.safeParse({ feedback_id: "f1", updated: "yes" }).success).toBe(false);
});

test("the answer as the form reads it: updated on a repeat, the server's message, a fixed one otherwise", () => {
  expect(feedbackFrom({ ok: true, status: 200, data: { feedback_id: "f1", updated: true } })).toEqual({ ok: true, updated: true });
  expect(feedbackFrom({ ok: false, status: 422, code: "invalid_feedback", message: "기타를 고르면 이유를 입력해야 합니다." }))
    .toEqual({ ok: false, code: "invalid_feedback", message: "기타를 고르면 이유를 입력해야 합니다." });
  expect(feedbackFrom({ ok: false, status: 429, code: "http_429", message: "서버 오류 (429)" }))
    .toMatchObject({ ok: false, code: "http_429", message: expect.stringContaining("잠시 뒤") });
  expect(feedbackFrom({ ok: false, status: null, code: "network", message: "x" })).toMatchObject({ ok: false, code: "network" });
});
