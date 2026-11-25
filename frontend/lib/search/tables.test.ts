import { expect, test } from "@jest/globals";

import { SearchOutput } from "./schemas.ts";
import { mergeTables, tablesQuery } from "./tables.ts";

const table = (key: string) => ({
  key, org_id: "101", tbl_id: key, table_name: `표 ${key}`, survey_name: "조사", organization: "국가데이터처",
  period: "년 (2015 ~ 2025)", url: "https://kosis.kr",
});

test("picked tables come first, candidates follow without repeating a picked one", () => {
  const out = SearchOutput.parse({ question: "q", route: "found", picked: [table("B")], candidates: [table("A"), table("B"), table("C")] });
  expect(mergeTables(out).map((t) => t.key)).toEqual(["B", "A", "C"]);
});

test("server prefetch and browser share one key per question", () => {
  expect(tablesQuery("출생아 수").queryKey).toEqual(["tables", "출생아 수"]);
});

test("a found result stays fresh, a failure is stale at once (retried on the next visit)", () => {
  const staleTime = tablesQuery("q").staleTime as (q: { state: { data?: unknown } }) => number;
  const out = SearchOutput.parse({ question: "q", route: "found" });
  expect(staleTime({ state: { data: { ok: true, out } } })).toBeGreaterThan(0);
  expect(staleTime({ state: { data: { ok: false, message: "x" } } })).toBe(0);
});

test("search request: a question of 1..200 characters only", async () => {
  const { SearchRequest } = await import("./schemas.ts");
  expect(SearchRequest.parse({ question: " 출생아 수 " })).toEqual({ question: "출생아 수" });
  for (const bad of [{ question: "" }, { question: "가".repeat(201) }, { q: "x" }, null]) expect(SearchRequest.safeParse(bad).success).toBe(false);
  expect(SearchOutput.safeParse({ question: "q" }).success).toBe(false);
});
