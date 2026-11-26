import { expect, test } from "@jest/globals";

import {
  droppedFailure, EXHAUSTED, failureFromEvent, failureFromStatus, finalMessage, MAX_RECONNECTS, shouldReconnect,
} from "./reconnect.ts";

test("a retryable failure reconnects once, then gives up", () => {
  const f = failureFromEvent({ type: "TimeoutError", detail: "slow", retryable: true, request_id: "a".repeat(32) });
  expect(MAX_RECONNECTS).toBe(1);
  expect(shouldReconnect(f, 0)).toBe(true);
  expect(shouldReconnect(f, 1)).toBe(false);
  expect(f.requestId).toBe("a".repeat(32)); // sent as retry_of on the reconnect
  expect(finalMessage(f)).toBe(EXHAUSTED);
});

test("an error event without retryable true is not retried and shows its own text", () => {
  const f = failureFromEvent({ type: "ValueError", detail: "bad", retryable: false });
  expect(shouldReconnect(f, 0)).toBe(false);
  expect(finalMessage(f)).toBe("처리 중 오류: ValueError bad");
  expect(shouldReconnect(failureFromEvent({ type: "X", detail: "old backend" }), 0)).toBe(false);
});

test("422 is never retried; 5xx and a dropped connection are", () => {
  const bad = failureFromStatus(422, "질문은 200자까지입니다");
  expect(shouldReconnect(bad, 0)).toBe(false);
  expect(finalMessage(bad)).toBe("질문은 200자까지입니다");
  expect(shouldReconnect({ ...bad, retryable: true }, 0)).toBe(false); // 422 wins over a stray flag
  expect(shouldReconnect(failureFromStatus(503, "loading"), 0)).toBe(true);
  expect(shouldReconnect(failureFromStatus(404, "nope"), 0)).toBe(false);
  expect(shouldReconnect(droppedFailure(null), 0)).toBe(true);
  expect(finalMessage(failureFromStatus(503, "loading"))).toBe(EXHAUSTED);
});
