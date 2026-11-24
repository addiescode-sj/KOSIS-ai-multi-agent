import { expect, test } from "@jest/globals";

import { formatDay, groupByDay } from "./history.ts";

const item = (id: string, iso: string) => ({ id, title: id, updatedAt: Date.parse(iso), questions: 1 });

test("groups by Korean day, newest first: 오늘 · 어제 · 지난 7일 · 이전", () => {
  const now = Date.parse("2026-11-24T10:00:00+09:00");
  const groups = groupByDay([
    item("old", "2026-11-10T12:00:00+09:00"),
    item("today", "2026-11-24T08:00:00+09:00"),
    item("week", "2026-11-19T12:00:00+09:00"),
    item("yesterday", "2026-11-23T23:30:00+09:00"),
  ], now);
  expect(groups.map((g) => [g.label, g.items.map((i) => i.id)])).toEqual([
    ["오늘", ["today"]], ["어제", ["yesterday"]], ["지난 7일", ["week"]], ["이전", ["old"]],
  ]);
});

test("the server's UTC time counts in Korea: 2026-11-23T16:00Z is already the 24th", () => {
  const now = Date.parse("2026-11-24T09:00:00+09:00");
  expect(groupByDay([item("a", "2026-11-23T16:00:00Z")], now)[0].label).toBe("오늘");
  expect(groupByDay([item("b", "2026-11-23T14:59:00Z")], now)[0].label).toBe("어제");
  expect(formatDay(Date.parse("2026-11-23T16:00:00Z"))).toBe("2026.11.24");
});
