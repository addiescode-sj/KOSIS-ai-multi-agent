import { expect, test } from "@jest/globals";

import { apiQuery, DEFAULT_FILTER, filterFrom, filterQuery, kstToday, rangeOf } from "./filter.ts";
import { count, dotDate, share, shortDate, signed, withUnit } from "./format.ts";
import { AdminMetrics } from "./schemas.ts";
import { toView } from "./view.ts";

test("filter: read from the address, unknown or missing values fall back to the default", () => {
  expect(filterFrom({})).toEqual(DEFAULT_FILTER);
  expect(filterFrom({ period: "30d", user: "guest", internal: "1" })).toEqual({ period: "30d", user: "guest", internal: true });
  expect(filterFrom({ period: "today", user: ["member", "guest"], internal: "yes" })).toEqual({ ...DEFAULT_FILTER, user: "member" });
  const f = { period: "yesterday", user: "member", internal: true } as const;
  expect(filterFrom(new URLSearchParams(filterQuery(f)))).toEqual(f);
});

test("periods end yesterday (KST), as the operator app's metrics do", () => {
  expect(rangeOf("yesterday", "2026-11-27")).toEqual({ start: "2026-11-26", end: "2026-11-26" });
  expect(rangeOf("7d", "2026-11-27")).toEqual({ start: "2026-11-20", end: "2026-11-26" });
  expect(rangeOf("30d", "2026-03-01")).toEqual({ start: "2026-01-30", end: "2026-02-28" }); // Jan 30-31 + 28 days
  expect(apiQuery({ period: "7d", user: "guest", internal: false }, "2026-11-27")).toBe("start=2026-11-20&end=2026-11-26&user=guest&internal=false");
  expect(kstToday(new Date("2026-11-26T15:30:00Z"))).toBe("2026-11-27"); // 00:30 in Korea is already the next day
});

test("format: units, signs, shares and dates", () => {
  expect(withUnit(2841, "건")).toBe("2,841건");
  expect(withUnit(6.4, "%")).toBe("6.4%");
  expect(signed(128, "건")).toBe("+128");
  expect(signed(-1.2, "%p")).toBe("-1.2%p");
  expect(share(412, 988)).toBe("41.7%");
  expect(count(1204)).toBe("1,204");
  expect(dotDate("2026-11-19")).toBe("2026.11.19");
  expect(shortDate("2026-11-09")).toBe("11/09");
});

// A made-up operator app answer in the shape of GET /admin/api/metrics (log_metrics rows as JSON)
const summary = (n: number, refusedShare: number, ratio: number | null) => ({
  question_count: [{ n }],
  outcome_ratio: n ? [{ outcome: "answered", n: n - 1, share: 1 - refusedShare }, { outcome: "refused", n: 1, share: refusedShare }] : [],
  thumbs_up_ratio: [{ up: 3, down: 1, ratio }],
  latency: [{ p50: 1000, p95: 2000 }],
});
const answer = {
  filter: { start: "2026-11-20", end: "2026-11-26", user: "all", internal: false },
  notice: null,
  read_at: "2026-11-27T05:00:00+00:00",
  labels: { outcome: { answered: "답함", refused: "거절" }, reason: { no_period: "기간 없음" }, up: { easy: "이해하기 쉬움" }, down: {} },
  tabs: {
    summary: { rows: summary(10, 0.1, 0.75), daily: { question_count: [{ day: "2026-11-25", n: 4 }, { day: "2026-11-26" }] } },
    quality: { rows: { refusal_reasons: [{ reason_code: "no_period", n: 1 }], up_reasons: [{ choice: "easy", n: 3 }], down_reasons: [{ choice: "new_code", n: 1 }], failed_stages: [], rewrite_kinds: [] } },
    usage: { rows: {
      top_tables: [{ picked_key: "101_X", survey_name: null, n: 9 }],
      turns_per_conversation: [{ turns: "1", n: 6 }, { turns: "2", n: 1 }, { turns: "4+", n: 1 }],
      citation_click_rate: [{ clicked: 2, with_numbers: 8, rate: 0.25 }],
      abort_rate: [{ aborted: 1, questions: 11, rate: 0.0909 }],
    } },
    cost: { rows: { llm_usage: [{ calls: 2.4, input_tokens: 4210.5, output_tokens: 612 }] } },
  },
  first_token: [{ p50: 2640, p95: 7980 }],
  previous: summary(8, 0.125, 0.8),
};

test("the operator app's answer becomes the cards: its numbers as they are, nothing made up", () => {
  const v = toView(AdminMetrics.parse(answer));
  const stat = Object.fromEntries(v.stats.map((s) => [s.key, s]));
  expect(stat.questions).toMatchObject({ value: 10, delta: 2, good: true });
  expect(stat.refusal_rate).toMatchObject({ value: 10, delta: -2.5, good: true }); // 10.0% vs 12.5%
  expect(stat.thumbs_up_rate).toMatchObject({ value: 75, delta: -5, good: false, note: "피드백 4건 중" });
  expect(stat.accuracy).toMatchObject({ value: null, delta: null }); // no stored source
  expect(v.answerTypes).toEqual([{ label: "답함", count: 9 }, { label: "거절", count: 1 }]);
  expect(v.refusals).toEqual([{ code: "no_period", label: "기간 없음", count: 1 }]);
  expect(v.downReasons).toEqual([{ label: "new_code", count: 1 }]); // an unknown code is shown as is
  expect(v.daily).toEqual([{ day: "2026-11-25", count: 4 }, { day: "2026-11-26", count: 0 }]);
  expect(v.rates.citationClick.value).toBe(25);
  expect(v.rates.multiTurn.value).toBe(25); // 2 of 8 conversations asked more than once
  expect(v.rates.dropBeforeAnswer.value).toBe(9.1);
  expect(v.tables).toEqual([{ key: "101_X", survey: "-", uses: 9 }]);
  expect(v.ttft).toEqual({ p50: 2640, p95: 7980 });
  expect(v.llm).toMatchObject({ calls: 2.4, costUsd: null });
  expect(v.latency).toEqual({ p50: 1000, p95: 2000 });
  expect([v.hourly, v.logQuality]).toEqual([null, null]);
});

test("no range before (90-day limit) and no questions: no change, no rate", () => {
  const empty = { ...answer, previous: null, tabs: { ...answer.tabs, summary: { rows: summary(0, 0, null), daily: { question_count: [] } } } };
  const v = toView(AdminMetrics.parse(empty));
  expect(v.stats.map((s) => s.delta)).toEqual([null, null, null, null]);
  expect(v.stats.find((s) => s.key === "refusal_rate")!.value).toBeNull();
  expect(v.stats.find((s) => s.key === "thumbs_up_rate")!.value).toBeNull();
  expect(v.answerTypes).toEqual([]);
});

test("an answer missing a metric the screen draws fails the check", () => {
  const broken = { ...answer, tabs: { ...answer.tabs, cost: { rows: {} } } };
  expect(AdminMetrics.safeParse(broken).success).toBe(false);
});
