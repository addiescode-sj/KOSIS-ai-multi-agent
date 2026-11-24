// The adapter is the only guard on the drawn chart: T3-37 grades the make_chart call, not ChatResult.chart.
// ChatResult.chart is the drawn chart as data (phase 1 ChartSpec JSON, KOS-202: no Vega-Lite).
import { expect, test } from "@jest/globals";

import type { ChartView } from "../../types/chart.ts";

import { toChartView } from "./chart.ts";
import { CHARTS } from "./fixtures.ts";

// Fails the test and narrows the type for the lines after it (what node:assert ok did)
function ok(condition: unknown): asserts condition {
  expect(condition).toBe(true);
}

const view = (spec: object) => toChartView(spec);
const labels = (v: ChartView) => ("points" in v ? v.points.map((p) => p.label) : []);

test("horizontal bar: sort -x = value descending, display strings and highlight kept, unit from the spec", () => {
  const v = view(CHARTS.bar);
  expect(v.kind).toBe("bar");
  ok(v.kind === "bar" && v.horizontal && v.unitTitle === "값 (명)" && v.title === "2023년 시도별 합계출산율");
  expect(labels(v)).toEqual(["세종", "부산", "서울"]);
  ok(v.kind === "bar");
  expect(v.points.map((p) => [p.value, p.display, p.highlight])).toEqual([[0.97, "0.97명", false], [0.66, "0.66명", false], [0.55, "0.55명", true]]);
});

test("vertical bar with a name list: that order", () => {
  const v = view(CHARTS.barVertical);
  ok(v.kind === "bar" && !v.horizontal && v.unitTitle === "값 (명)");
  expect(labels(v)).toEqual(["서울", "부산", "세종"]);
});

test("timeline: bars in the backend's order, only the asked period highlighted", () => {
  const v = view(CHARTS.timeline);
  expect(v.kind).toBe("timeline");
  expect(labels(v)).toEqual(["2021", "2022", "2023"]);
  ok(v.kind === "timeline");
  expect(v.points.map((p) => p.highlight)).toEqual([false, false, true]);
  expect(v.points[2].display).toBe("230,028명");
});

test("line: periods ascending, every point kept", () => {
  const v = view(CHARTS.line);
  expect(v.kind).toBe("line");
  expect(labels(v)).toEqual(["2021", "2022", "2023"]);
  ok(v.kind === "line");
  expect(v.points.map((p) => p.value)).toEqual([0.81, 0.78, 0.72]);
});

test("pie: slices in the backend's order with their display strings", () => {
  const v = view(CHARTS.pie);
  ok(v.kind === "pie");
  expect(v.points.map((p) => [p.label, p.display, p.highlight])).toEqual([["1인 가구", "750만 가구", true], ["그 외", "1,360만 가구", false]]);
});

test("grouped bar and multi line: one series per target, aligned on the periods", () => {
  for (const [spec, kind] of [[CHARTS.grouped_bar, "grouped_bar"], [CHARTS.multi_line, "multi_line"]] as const) {
    const v = view(spec);
    ok(v.kind === kind);
    ok(v.kind === "grouped_bar" || v.kind === "multi_line");
    expect(v.periods).toEqual(["2022", "2023"]);
    expect(v.series.map((s) => s.name)).toEqual(["서울", "부산"]);
    expect(v.series.map((s) => s.points.map((p) => p?.display))).toEqual([["0.59명", "0.55명"], ["0.72명", "0.66명"]]);
  }
});

test("a grid target missing a period gets a gap, not a made-up point", () => {
  const spec = structuredClone(CHARTS.grouped_bar);
  spec.series[1].points = spec.series[1].points.filter((p) => p.label !== "2022");
  const v = view(spec);
  ok(v.kind === "grouped_bar");
  expect(v.series[1].points[0]).toBe(null);
});

test("a symbol cell (값 null) stays last when sorting by value and keeps its display", () => {
  const spec = structuredClone(CHARTS.bar);
  spec.series[0].points.push({ label: "대구", value: null, display: "-", fact_id: null, period: null, highlight: false, share_pct: null });
  const v = view(spec);
  expect(labels(v)).toEqual(["세종", "부산", "서울", "대구"]);
  ok(v.kind === "bar" && v.points[3].value === null && v.points[3].display === "-");
});

test("an unknown kind shows its points as a table; a chart that does not parse gives the fixed sentence", () => {
  const custom = { kind: "area", title: "사용자 정의", series: [{ name: "s", points: [{ label: "가", value: 1, display: "1건" }] }] };
  const v = view(custom);
  ok(v.kind === "table");
  expect([v.columns, v.rows]).toEqual([["이름", "값"], [["가", "1건"]]]);
  expect(view({ title: "x", kind: "bar", series: [] }).kind).toBe("none");
  // an old answer saved with a Vega-Lite chart (before KOS-202) is not drawn
  expect(view({ $schema: "https://vega.github.io/schema/vega-lite/v5.json", title: "옛 차트", data: { values: [] } })).toEqual({ kind: "none", title: "옛 차트" });
});

test("stacked bar (T3-02 composition change): per period the part then its rest, display strings kept, no table fallback", () => {
  const v = view(CHARTS.stacked_bar);
  expect(v.kind).toBe("stacked_bar");
  ok(v.kind === "stacked_bar" && v.unitTitle === "값 (%)" && v.title === "서울 1인 가구 비중 비교");
  expect(v.periods).toEqual(["2015년", "2023년"]);
  expect(v.series.map((s) => s.name)).toEqual(["서울 1인 가구", "그 외"]);
  expect(v.series.map((s) => s.points.map((p) => p?.display))).toEqual([["29.5%", "39.3%"], ["70.5%", "60.7%"]]);
  ok(v.series.every((s) => s.points.every((p) => p?.highlight === false))); // coloured by part, not by highlight
  // the bars add up to 100 in every period: nothing here computes, the rest is the backend's second series
  expect(v.periods.map((_, i) => v.series.reduce((sum, s) => sum + (s.points[i]?.value ?? 0), 0))).toEqual([100, 100]);
});
