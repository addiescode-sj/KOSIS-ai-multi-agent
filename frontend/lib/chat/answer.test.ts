import { expect, test } from "@jest/globals";

import type { ChatMessage } from "../../types/chat.ts";
import { answerKind, citation, formatValue, headline, progress, refusalReason, refusalTitle, REFUSALS } from "./answer.ts";
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
  expect(headline(parse(ANSWERED))).toEqual({ value: "100", unit: "명", period: "2024" });
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
    survey: "조사", table: "표 DT_A", url: "https://kosis.kr", text: "KOSIS / 조사 / 표 DT_A",
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
