// Run: node --test lib/*.test.ts (Node 24 strips the types)
import assert from "node:assert/strict";
import { test } from "node:test";

import { answerKind, citation, formatValue, headline, progress, refusalReason, refusalTitle, REFUSALS } from "./answer.ts";
import type { ChatMessage } from "./chat-types.ts";
import { ANSWERED, CHART_FAILED, CLARIFY, PARTIAL, REFUSED, SEARCH_REFUSED } from "./fixtures.ts";
import { ChatResult } from "./schemas.ts";

const parse = (x: unknown) => ChatResult.parse(x);

test("every backend outcome parses and picks its card", () => {
  assert.equal(answerKind(parse(ANSWERED)), "answered");
  assert.equal(answerKind(parse(REFUSED)), "refused");
  assert.equal(answerKind(parse(SEARCH_REFUSED)), "refused");
  assert.equal(answerKind(parse(CLARIFY)), "clarify");
  assert.equal(answerKind(parse(PARTIAL)), "partial"); // answered overall, one sub-answer refused
  assert.equal(answerKind(parse(CHART_FAILED)), "failed");
});

test("a clarify sub-answer also makes the turn partial; all answered stays answered", () => {
  const subs = PARTIAL.sub_results;
  assert.equal(answerKind(parse({ ...PARTIAL, sub_results: [subs[0], { ...subs[1], outcome: "clarify" }] })), "partial");
  assert.equal(answerKind(parse({ ...PARTIAL, sub_results: [subs[0], subs[0]] })), "answered");
});

test("records without outcome (before T3-23) fall back to no_answer", () => {
  assert.equal(answerKind(parse({ ...ANSWERED, outcome: undefined })), "answered");
  assert.equal(answerKind(parse({ ...REFUSED, outcome: undefined })), "refused");
});

test("the headline is the first cited fact as phase 1 computed it, never on a refusal", () => {
  assert.deepEqual(headline(parse(ANSWERED)), { value: "100", unit: "명", period: "2024" });
  assert.equal(headline(parse(REFUSED)), null);
  assert.equal(headline(parse(PARTIAL)), null);
  const symbol = structuredClone(ANSWERED);
  symbol.stat_answer.result.facts[0] = { ...symbol.stat_answer.result.facts[0], value: null as never, raw: "-" as never };
  assert.equal(headline(parse(symbol)), null); // a symbol cell has no number to show
});

test("display keeps a derived fact's rounding and a source cell's digits", () => {
  assert.equal(formatValue({ value: 230028, round: null }), "230,028");
  assert.equal(formatValue({ value: 0.72, round: null }), "0.72");
  assert.equal(formatValue({ value: -7.7, round: 1 }), "-7.7");
  assert.equal(formatValue({ value: 12, round: 2 }), "12.00");
});

test("citation: KOSIS / survey / table with the KOSIS link of the same table", () => {
  assert.deepEqual(citation(parse(ANSWERED)), {
    survey: "조사", table: "표 DT_A", url: "https://kosis.kr", text: "KOSIS / 조사 / 표 DT_A",
  });
  assert.equal(citation(parse(SEARCH_REFUSED)), null); // no table behind a search refusal
});

test("refusal reasons: phase 1 reason first, then the search reason; every phase 1 reason has a title", () => {
  assert.equal(refusalReason(parse(REFUSED)), "no_period");
  assert.equal(refusalReason(parse(SEARCH_REFUSED)), "out_of_scope");
  for (const r of ["no_period", "no_item", "class_mismatch", "unit_mismatch", "not_aggregatable", "causal", "missing_value", "out_of_scope"]) {
    assert.ok(REFUSALS[r], r);
  }
  assert.equal(refusalTitle("something_new").title, "이 질문에는 답할 수 없습니다");
});

test("progress follows the stream: search nodes, stat, result", () => {
  const at = (...parts: ChatMessage["parts"]) => progress({ parts });
  assert.equal(at(), 0);
  assert.equal(at({ type: "data-node", data: { name: "parse", seconds: 0.1 } }), 1);
  assert.equal(at({ type: "data-node", data: { name: "gate", seconds: 0.1 } }), 2);
  assert.equal(at({ type: "data-stat", data: { key: "101_DT_A", seconds: 1 } }), 3);
  assert.equal(at({ type: "data-result", data: parse(ANSWERED) }), 4);
});

test("an unknown backend field passes through, a wrong type is caught", () => {
  assert.equal((parse({ ...ANSWERED, new_field: 1 }) as Record<string, unknown>).new_field, 1);
  assert.throws(() => parse({ ...ANSWERED, outcome: "maybe" }));
  assert.throws(() => parse({ ...ANSWERED, picked: "101_DT_A" }));
});
