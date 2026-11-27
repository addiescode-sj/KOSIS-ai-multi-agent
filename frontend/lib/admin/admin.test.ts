import { expect, test } from "@jest/globals";

import { DEFAULT_FILTER, filterFrom, filterQuery } from "./filter.ts";
import { count, dotDate, share, shortDate, signed, withUnit } from "./format.ts";
import { sampleDashboard, sampleTrace } from "./sample.ts";
import { AdminDashboard, AdminTrace } from "./schemas.ts";

test("filter: read from the address, unknown or missing values fall back to the default", () => {
  expect(filterFrom({})).toEqual(DEFAULT_FILTER);
  expect(filterFrom({ period: "30d", user: "guest", internal: "0" })).toEqual({ period: "30d", user: "guest", internal: false });
  expect(filterFrom({ period: "1y", user: ["member", "guest"], internal: "yes" })).toEqual({ ...DEFAULT_FILTER, user: "member" });
  const f = { period: "7d", user: "member", internal: false } as const;
  expect(filterFrom(new URLSearchParams(filterQuery(f)))).toEqual(f);
});

test("format: units, signs, shares and dates", () => {
  expect(withUnit(2841, "건")).toBe("2,841건");
  expect(withUnit(6.4, "%")).toBe("6.4%");
  expect(signed(128, "건")).toBe("+128");
  expect(signed(-1.2, "%p")).toBe("-1.2%p");
  expect(share(412, 988)).toBe("41.7%");
  expect(share(1, 0)).toBe("0.0%");
  expect(count(1204)).toBe("1,204");
  expect(dotDate("2026-11-19")).toBe("2026.11.19");
  expect(shortDate("2026-11-09")).toBe("11/09");
});

test("sample data passes the screens' schema and agrees with itself", () => {
  const d = AdminDashboard.parse(sampleDashboard(DEFAULT_FILTER));
  const sum = (xs: { count: number }[]) => xs.reduce((a, x) => a + x.count, 0);
  const questions = d.stats.find((s) => s.key === "questions")!.value;
  expect(sum(d.answerTypes)).toBe(questions); // every question has one answer type
  expect(d.daily.at(-1)!.count).toBe(questions); // today's point is today's total
  expect(sum(d.refusals)).toBe(d.answerTypes.find((a) => a.label === "거절")!.count);
  expect(d.hourly).toHaveLength(7);
  for (const row of d.hourly) expect(row).toHaveLength(8);
  expect(AdminTrace.parse(sampleTrace("x")).spans.every((s) => s.start <= s.end)).toBe(true);
});
