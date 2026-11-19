// The adapter is the only guard on the drawn chart: T3-37 grades the make_chart call, not ChatResult.chart.
import { expect, test } from "@jest/globals";

import type { ChartView } from "../../types/chart.ts";

import { toChartView } from "./chart.ts";
import { VEGA } from "./fixtures.ts";

// Fails the test and narrows the type for the lines after it (what node:assert ok did)
function ok(condition: unknown): asserts condition {
  expect(condition).toBe(true);
}

const view = (spec: object) => toChartView(spec as Record<string, unknown>);
const labels = (v: ChartView) => ("points" in v ? v.points.map((p) => p.label) : []);

test("horizontal bar: sort -x = value descending, display strings and highlight kept, unit from x.title", () => {
  const v = view(VEGA.bar);
  expect(v.kind).toBe("bar");
  ok(v.kind === "bar" && v.horizontal && v.unitTitle === "값 (명)" && v.title === "2023년 시도별 합계출산율");
  expect(labels(v)).toEqual(["세종", "부산", "서울"]);
  ok(v.kind === "bar");
  expect(v.points.map((p) => [p.value, p.display, p.highlight])).toEqual([[0.97, "0.97명", false], [0.66, "0.66명", false], [0.55, "0.55명", true]]);
});

test("vertical bar with a name list: that order, unit from y.title", () => {
  const v = view(VEGA.barVertical);
  ok(v.kind === "bar" && !v.horizontal && v.unitTitle === "값 (명)");
  expect(labels(v)).toEqual(["서울", "부산", "세종"]);
});

test("timeline: bars in data order (sort null), only the asked period highlighted", () => {
  const v = view(VEGA.timeline);
  expect(v.kind).toBe("timeline");
  expect(labels(v)).toEqual(["2021", "2022", "2023"]);
  ok(v.kind === "timeline");
  expect(v.points.map((p) => p.highlight)).toEqual([false, false, true]);
  expect(v.points[2].display).toBe("230,028명");
});

test("line: periods ascending like Vega's ordinal axis, every row kept", () => {
  const v = view(VEGA.line);
  expect(v.kind).toBe("line");
  expect(labels(v)).toEqual(["2021", "2022", "2023"]);
  ok(v.kind === "line");
  expect(v.points.map((p) => p.value)).toEqual([0.81, 0.78, 0.72]);
});

test("pie: slices in 순서 order with their display strings", () => {
  const v = view(VEGA.pie);
  ok(v.kind === "pie");
  expect(v.points.map((p) => [p.label, p.display, p.highlight])).toEqual([["1인 가구", "750만 가구", true], ["그 외", "1,360만 가구", false]]);
});

test("grouped bar and multi line: one series per 대상, aligned on the periods", () => {
  for (const [spec, kind] of [[VEGA.grouped_bar, "grouped_bar"], [VEGA.multi_line, "multi_line"]] as const) {
    const v = view(spec);
    ok(v.kind === kind);
    ok(v.kind === "grouped_bar" || v.kind === "multi_line");
    expect(v.periods).toEqual(["2022", "2023"]);
    expect(v.series.map((s) => s.name)).toEqual(["서울", "부산"]);
    expect(v.series.map((s) => s.points.map((p) => p?.display))).toEqual([["0.59명", "0.55명"], ["0.72명", "0.66명"]]);
  }
});

test("a grid target missing a period gets a gap, not a made-up point", () => {
  const spec = structuredClone(VEGA.grouped_bar) as { data: { values: Record<string, unknown>[] } };
  spec.data.values = spec.data.values.filter((r) => !(r["대상"] === "부산" && r["시점"] === "2022"));
  const v = view(spec);
  ok(v.kind === "grouped_bar");
  expect(v.series[1].points[0]).toBe(null);
});

test("a symbol cell (값 null) stays last when sorting by value and keeps its display", () => {
  const spec = structuredClone(VEGA.bar) as { data: { values: Record<string, unknown>[] } };
  spec.data.values.push({ 이름: "대구", 값: null, 표시: "-", 강조: "아니오" });
  const v = view(spec);
  expect(labels(v)).toEqual(["세종", "부산", "서울", "대구"]);
  ok(v.kind === "bar" && v.points[3].value === null && v.points[3].display === "-");
});

test("an unknown spec shows its rows as a table; no rows at all gives the fixed sentence", () => {
  const custom = { title: "사용자 정의", mark: { type: "area" }, data: { values: [{ 이름: "가", 값: 1, 표시: "1건" }] } };
  const v = view(custom);
  ok(v.kind === "table");
  expect([v.columns, v.rows]).toEqual([["이름", "값"], [["가", "1건"]]]);
  expect(view({ title: "x", mark: "bar" }).kind).toBe("none");
  expect(view({ data: { url: "data.csv" } }).kind).toBe("none");
});
