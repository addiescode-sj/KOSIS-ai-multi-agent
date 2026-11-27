import { expect, test } from "@jest/globals";

import { ANSWER_ERRORS } from "./errors.ts";
import {
  droppedFailure, failureFromEvent, failureFromStatus, finalMessage, MAX_RECONNECTS, shouldReconnect, unreachableFailure,
} from "./reconnect.ts";

test("a retryable failure reconnects once, then gives up with its code's sentence", () => {
  const f = failureFromEvent({ code: "timeout", message: "m", retryable: true, request_id: "a".repeat(32) });
  expect(MAX_RECONNECTS).toBe(1);
  expect(shouldReconnect(f, 0)).toBe(true);
  expect(shouldReconnect(f, 1)).toBe(false);
  expect(f.requestId).toBe("a".repeat(32)); // sent as retry_of on the reconnect
  expect(finalMessage(f)).toBe(ANSWER_ERRORS.timeout.text);
});

test("T3-67: the exception name and text never reach the card", () => {
  const f = failureFromEvent({ code: "config_error", message: "설정", retryable: false, type: "LLMKeyMissing",
                               detail: "HCX_API_KEY is not set" } as never);
  expect(shouldReconnect(f, 0)).toBe(false);
  expect(finalMessage(f)).toBe(ANSWER_ERRORS.config_error.text);
  // an older backend: no code, no retryable -> not retried, the generic sentence (not "처리 중 오류: ValueError ...")
  const old = failureFromEvent({ type: "ValueError", detail: "bad 10.0.0.5" } as never);
  expect(shouldReconnect(old, 0)).toBe(false);
  expect(finalMessage(old)).toBe(ANSWER_ERRORS.internal_error.text);
});

test("a code the screen does not know shows the server's sentence", () => {
  expect(finalMessage(failureFromEvent({ code: "brand_new", message: "새 오류 문구", retryable: true }))).toBe("새 오류 문구");
});

test("422 is never retried and keeps the input explanation; 5xx and a dropped connection are retried", () => {
  const bad = failureFromStatus(422, { message: "질문은 200자까지입니다" });
  expect(shouldReconnect(bad, 0)).toBe(false);
  expect(finalMessage(bad)).toBe("질문은 200자까지입니다");
  expect(shouldReconnect({ ...bad, retryable: true }, 0)).toBe(false); // 422 wins over a stray flag
  expect(shouldReconnect(failureFromStatus(503, { code: "unavailable", message: "x" }), 0)).toBe(true);
  expect(shouldReconnect(failureFromStatus(404, { code: "not_found", message: "nope" }), 0)).toBe(false);
  expect(shouldReconnect(droppedFailure(null), 0)).toBe(true);
  expect(finalMessage(droppedFailure(null))).toBe(ANSWER_ERRORS.connection_lost.text);
  // the backend not reachable at all is not "the connection broke": the service is unavailable for now
  expect(shouldReconnect(unreachableFailure(), 0)).toBe(true);
  expect(finalMessage(unreachableFailure())).toBe(ANSWER_ERRORS.backend_unreachable.text);
});

test("a 503 says why when the backend gave a cause; an entry proxy's page (no JSON) is the service being unreachable", () => {
  expect(failureFromStatus(503, { code: "unavailable", message: "x", cause: "model_unavailable" }).code).toBe("model_unavailable");
  expect(finalMessage(failureFromStatus(503, { code: "unavailable", message: "x" }))).toBe(ANSWER_ERRORS.backend_unreachable.text);
  expect(failureFromStatus(502, { code: "http_502", message: "" }).code).toBe("backend_unreachable");
});
