import { expect, test } from "@jest/globals";

import type { ChatMessage } from "../../types/chat.ts";
import {
  answerKind, cardHeadline, citation, copyText, formatValue, headline, resultOf, progress, refusalReason, refusalTitle, REFUSALS, requestIdOf, threadIdOf,
  GeneratingStatus,
  turnState,
} from "./answer.ts";
import { ANSWERED, CHART_FAILED, CLARIFY, PARTIAL, REFUSED, SEARCH_REFUSED } from "./fixtures.ts";
import { ChatResult } from "./schemas.ts";

const parse = (x: unknown) => ChatResult.parse(x);

test("every backend outcome parses and picks its card", () => {
  expect(answerKind(parse(ANSWERED))).toBe("answered");
  expect(answerKind(parse(REFUSED))).toBe("refused");
  expect(answerKind(parse(SEARCH_REFUSED))).toBe("refused");
  expect(answerKind(parse(CLARIFY))).toBe("clarify");
  expect(answerKind(parse(PARTIAL))).toBe("partial"); // answered overall, one sub-answer refused
  expect(answerKind(parse(CHART_FAILED))).toBe("failed");
});

test("a clarify sub-answer also makes the turn partial; all answered stays answered", () => {
  const subs = PARTIAL.sub_results;
  expect(answerKind(parse({ ...PARTIAL, sub_results: [subs[0], { ...subs[1], outcome: "clarify" }] }))).toBe("partial");
  expect(answerKind(parse({ ...PARTIAL, sub_results: [subs[0], subs[0]] }))).toBe("answered");
});

test("records without outcome (before T3-23) fall back to no_answer", () => {
  expect(answerKind(parse({ ...ANSWERED, outcome: undefined }))).toBe("answered");
  expect(answerKind(parse({ ...REFUSED, outcome: undefined }))).toBe("refused");
});

test("the headline is the first cited fact as phase 1 computed it, never on a refusal", () => {
  expect(headline(parse(ANSWERED))).toEqual({ value: "100", raw: 100, round: null, unit: "명", period: "2024" });
  expect(headline(parse(REFUSED))).toBeNull();
  expect(headline(parse(PARTIAL))).toBeNull();
  const symbol = structuredClone(ANSWERED);
  symbol.stat_answer.result.facts[0] = { ...symbol.stat_answer.result.facts[0], value: null as never, raw: "-" as never };
  expect(headline(parse(symbol))).toBeNull(); // a symbol cell has no number to show
});

test("display keeps a derived fact's rounding and a source cell's digits", () => {
  expect(formatValue({ value: 230028, round: null })).toBe("230,028");
  expect(formatValue({ value: 0.72, round: null })).toBe("0.72");
  expect(formatValue({ value: -7.7, round: 1 })).toBe("-7.7");
  expect(formatValue({ value: 12, round: 2 })).toBe("12.00");
});

test("citation: KOSIS / survey / table with the KOSIS link of the same table", () => {
  expect(citation(parse(ANSWERED))).toEqual({
    survey: "조사", table: "표 DT_A", url: "https://kosis.kr", text: "KOSIS / 조사 / 표 DT_A", key: "101_DT_A", rank: 1,
  });
  expect(citation(parse(SEARCH_REFUSED))).toBeNull(); // no table behind a search refusal
});

test("refusal reasons: phase 1 reason first, then the search reason; every phase 1 reason has a title", () => {
  expect(refusalReason(parse(REFUSED))).toBe("no_period");
  expect(refusalReason(parse(SEARCH_REFUSED))).toBe("out_of_scope");
  for (const r of ["no_period", "no_item", "class_mismatch", "unit_mismatch", "not_aggregatable", "causal", "missing_value", "out_of_scope"]) {
    expect(REFUSALS[r]).toBeDefined();
  }
  expect(refusalTitle("something_new").title).toBe("이 질문에는 답할 수 없습니다");
});

test("progress follows the stream: search nodes, stat, result", () => {
  const at = (...parts: ChatMessage["parts"]) => progress({ parts });
  expect(at()).toBe(0);
  expect(at({ type: "data-node", data: { name: "parse", seconds: 0.1 } })).toBe(1);
  expect(at({ type: "data-node", data: { name: "gate", seconds: 0.1 } })).toBe(2);
  expect(at({ type: "data-stat", data: { key: "101_DT_A", seconds: 1 } })).toBe(3);
  expect(at({ type: "data-result", data: parse(ANSWERED) })).toBe(4);
});

test("an unknown backend field passes through, a wrong type is caught", () => {
  expect((parse({ ...ANSWERED, new_field: 1 }) as Record<string, unknown>).new_field).toBe(1);
  expect(() => parse({ ...ANSWERED, outcome: "maybe" })).toThrow();
  expect(() => parse({ ...ANSWERED, picked: "101_DT_A" })).toThrow();
});

test("citation rank: 1-based among the picked tables, else the candidates, else null", () => {
  const other = { ...ANSWERED.picked[0], key: "101_DT_B", tbl_id: "DT_B" };
  const r = parse({ ...ANSWERED, picked: [other, ANSWERED.picked[0]] });
  expect(citation(r)?.rank).toBe(2);
  expect(citation(parse({ ...ANSWERED, picked: [other], candidates: [other, other, ANSWERED.picked[0]] }))?.rank).toBe(3);
  expect(citation(parse({ ...ANSWERED, picked: [], candidates: [] }))?.rank).toBeNull();
});

test("copy text: the backend answer as is, 출처 added only when the answer lacks it", () => {
  expect(copyText(parse(ANSWERED))).toBe(ANSWERED.answer); // already ends with its 출처 line
  expect(copyText(parse({ ...ANSWERED, answer: "100명입니다." }))).toBe("100명입니다.\n\n출처: KOSIS / 조사 / 표 DT_A (https://kosis.kr)");
  expect(copyText(parse({ ...SEARCH_REFUSED, answer: " 범위 밖 " }))).toBe("범위 밖");
});

test("request id: the result's own, else the stream meta; thread id from the meta", () => {
  const withMeta: Pick<ChatMessage, "parts"> = { parts: [{ type: "data-meta", data: { threadId: "t1", requestId: "m".repeat(32) } }] };
  expect(requestIdOf(withMeta)).toBe("m".repeat(32));
  expect(threadIdOf(withMeta)).toBe("t1");
  const done: Pick<ChatMessage, "parts"> = {
    parts: [...withMeta.parts, { type: "data-result", data: parse({ ...ANSWERED, request_id: "r".repeat(32) }) }],
  };
  expect(requestIdOf(done)).toBe("r".repeat(32));
  expect(requestIdOf({ parts: [] })).toBeNull();
  expect(threadIdOf({ parts: [] })).toBeNull();
});

test("turn state: reconnecting until the new attempt's meta, unfinished draft kept as incomplete", () => {
  const reconnect = (connected: boolean): Pick<ChatMessage, "parts"> => ({
    parts: [{ type: "data-draft", data: { text: "2024년 " } }, { type: "data-reconnect", data: { attempt: 1, connected } }],
  });
  expect(turnState({ parts: [] }, true)).toBe(GeneratingStatus.WORKING);
  expect(turnState(reconnect(false), true)).toBe(GeneratingStatus.RECONNECTING);
  expect(turnState(reconnect(true), true)).toBe(GeneratingStatus.WORKING);
  expect(turnState(reconnect(false), false)).toBe(GeneratingStatus.INCOMPLETE); // the reconnect failed too: tokens stay, marked
  expect(turnState({ parts: [] }, false)).toBe(GeneratingStatus.STOPPED);
});

test("the card's 핵심 수치: an answered one-question turn only (the chat scrolls to it first)", () => {
  expect(cardHeadline(parse(ANSWERED))?.value).toBe("100");
  expect(cardHeadline(parse(REFUSED))).toBeNull();
  expect(cardHeadline(parse(PARTIAL))).toBeNull(); // partial: no single number
  expect(cardHeadline(parse({ ...PARTIAL, sub_results: [PARTIAL.sub_results[0], PARTIAL.sub_results[0]] }))).toBeNull(); // multi, all answered
});

test("resultOf finds the turn's result part, none while it streams", () => {
  const result = parse(ANSWERED);
  const done = { parts: [{ type: "data-meta", data: { threadId: "t" } }, { type: "data-result", data: result }] } as unknown as ChatMessage;
  expect(resultOf(done)).toBe(result);
  expect(resultOf({ parts: [{ type: "data-meta", data: { threadId: "t" } }] } as unknown as ChatMessage)).toBeNull();
});
