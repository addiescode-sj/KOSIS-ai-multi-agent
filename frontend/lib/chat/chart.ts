// ChatResult.chart (the drawn chart as data: phase 1 ChartSpec JSON, kosis_chat/chart/module.py chart_data) → a view
// model Recharts draws (T3-02, KOS-202: no Vega-Lite). The kind is the backend's; values, display strings and
// highlights are the backend's points as they are: nothing here formats or computes a number (a stacked bar's rest
// to 100% comes as its second series). A chart of a kind this file does not know becomes a table of its points; one
// that does not parse becomes a fixed sentence.

import type { ChartView, Point } from "../../types/chart.ts";

import { ChartSpec } from "./schemas.ts";

type SpecPoint = ChartSpec["series"][number]["points"][number];

export function toChartView(input: unknown): ChartView {
  const parsed = ChartSpec.safeParse(input);
  if (!parsed.success) {
    const title = isRecord(input) && typeof input.title === "string" ? input.title : "";
    return { kind: "none", title };
  }
  const spec = parsed.data;
  const title = spec.title;
  const unitTitle = spec.unit ? `값 (${spec.unit})` : "값";
  const all = spec.series.flatMap((s) => s.points);
  const pt = (p: SpecPoint, label = p.label): Point => ({ label, value: p.value, display: p.display, highlight: p.highlight });
  switch (spec.kind) {
    case "bar": {
      const horizontal = spec.orientation !== "vertical";
      return { kind: "bar", title, unitTitle, horizontal, points: orderBars(all, spec.sort ?? "-x", horizontal).map((p) => pt(p)) };
    }
    case "timeline": // bars in period order, as the backend lists them
      return { kind: "timeline", title, unitTitle, points: all.map((p) => pt(p)) };
    case "line": {
      const ordered = [...all].sort((a, b) => compareText(str(a.period ?? a.label), str(b.period ?? b.label)));
      return { kind: "line", title, unitTitle, points: ordered.map((p) => pt(p, str(p.period ?? p.label))) };
    }
    case "pie":
      return { kind: "pie", title, points: all.map((p) => pt(p)) };
    case "grouped_bar":
    case "multi_line":
    case "stacked_bar": {
      const seen = firstSeen(all.map((p) => p.label));
      const periods = spec.kind === "grouped_bar" ? seen.sort(compareText) : seen;
      const series = spec.series.map((s) => ({
        name: s.name,
        points: periods.map((period) => {
          const p = s.points.find((q) => q.label === period);
          if (!p) return null;
          // a stacked bar colours by part (the share, then its rest), not by highlight
          return spec.kind === "stacked_bar" ? { ...pt(p, period), highlight: false } : pt(p, period);
        }),
      }));
      return { kind: spec.kind, title, unitTitle, periods, series };
    }
    default:
      return table(title, spec.series);
  }
}

// Bars in the order the backend asks for: a name list, else "-x" / "x" (by value for horizontal bars, by name for
// vertical ones); points without a value stay last
function orderBars<P extends { label: string; value: number | null }>(points: P[], sort: string | string[], horizontal: boolean): P[] {
  if (Array.isArray(sort)) {
    const at = (p: P) => {
      const i = sort.indexOf(p.label);
      return i < 0 ? sort.length : i;
    };
    return [...points].sort((a, b) => at(a) - at(b));
  }
  if (/^-?[xy]$/.test(sort)) {
    const onValue = (sort.replace("-", "") === "x") === horizontal;
    const sign = sort.startsWith("-") ? -1 : 1;
    const missing = (p: P) => (onValue ? p.value == null : false);
    const key = (p: P) => (onValue ? p.value : p.label);
    return [...points].sort((a, b) => Number(missing(a)) - Number(missing(b)) || sign * compareValues(key(a), key(b)));
  }
  return [...points].sort((a, b) => compareText(a.label, b.label));
}

// Fallback for a kind this file does not draw: the points themselves, with the display string the backend wrote
function table(title: string, series: ChartSpec["series"]): ChartView {
  if (typeof console !== "undefined") console.warn("chart: unknown chart kind, showing its points as a table");
  const many = series.length > 1;
  const columns = many ? ["대상", "이름", "값"] : ["이름", "값"];
  const rows = series.flatMap((s) => s.points.map((p) => (many ? [s.name, p.label, p.display] : [p.label, p.display])));
  return rows.length ? { kind: "table", title, columns, rows } : { kind: "none", title };
}

function firstSeen(items: string[]): string[] {
  return [...new Set(items)];
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown): string {
  return v == null ? "" : String(v);
}

function compareText(a: string, b: string): number {
  return a.localeCompare(b, "ko", { numeric: true });
}

function compareValues(a: unknown, b: unknown): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return compareText(str(a), str(b));
}
