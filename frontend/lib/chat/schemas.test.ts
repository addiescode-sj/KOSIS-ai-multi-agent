import { expect, test } from "@jest/globals";

import { ANSWERED } from "./fixtures.ts";
import { ChatResult, ChatStreamRequest, ErrorEvent, Meta, NodeEvent, StatEvent, TokenEvent, TurnFeedback } from "./schemas.ts";

test("/chat/stream request: 1..200 characters, the contract's input methods, no extra fields", () => {
  const ok = { question: "2023년 합계출산율은?", thread_id: null, retry_of: null, retry_kind: null, input_method: "direct" };
  expect(ChatStreamRequest.safeParse(ok).success).toBe(true);
  for (const bad of [
    { ...ok, question: "  " }, { ...ok, question: "가".repeat(201) }, { ...ok, input_method: "voice" },
    { ...ok, session_id: "x" }, { question: "q" }, { ...ok, retry_kind: "manual" },
  ]) expect(ChatStreamRequest.safeParse(bad).success).toBe(false);
});

test("SSE meta / token / stat / node / error events", () => {
  expect(Meta.parse({ thread_id: "t", trace_id: "4bf9" })).toMatchObject({ expired: false });
  expect(Meta.safeParse({ trace_id: "4bf9" }).success).toBe(false);
  expect(TokenEvent.safeParse({ delta: "2023년 " }).success).toBe(true);
  expect(TokenEvent.safeParse({ text: "x" }).success).toBe(false);
  expect(StatEvent.safeParse({ key: "101_DT_1B81A12", seconds: 0.4, spans: [] }).success).toBe(true);
  expect(StatEvent.safeParse({ key: "101_DT_1B81A12" }).success).toBe(false);
  expect(NodeEvent.parse({ seconds: 1.5, picked: [] }).seconds).toBe(1.5);
  expect(NodeEvent.parse(["a node's own output"]).seconds).toBe(0); // not ours to check: shown as 0 s
  expect(ErrorEvent.parse({ type: "TimeoutError", detail: "x" })).toMatchObject({ retryable: false });
  expect(ErrorEvent.safeParse({ retryable: "yes" }).success).toBe(false);
});

test("result: the ChatResult schema with request_id", () => {
  expect(ChatResult.parse({ ...ANSWERED, request_id: "4bf92f3577b34da6a3ce929d0e0e4736" }).request_id).toHaveLength(32);
  expect(ChatResult.safeParse({ ...ANSWERED, answer: undefined }).success).toBe(false);
  expect(TurnFeedback.safeParse({ value: "up", category: null }).success).toBe(true);
  expect(TurnFeedback.safeParse({ value: "up" }).success).toBe(false);
});
