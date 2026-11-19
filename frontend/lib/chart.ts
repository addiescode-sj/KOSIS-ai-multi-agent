// Phase 1 Vega-Lite spec (ChatResult.chart, kosis_chat/chart/vega.py `to_vega` at 907b703) → a view model Recharts draws
// (T3-11 Change 1). Values, display strings (`표시`) and highlights are the backend's rows as they are: nothing here
// formats or computes a number. A spec this file cannot read becomes a table of its rows, or a fixed sentence.
//
// Kinds are told apart by row fields first (plans/T3-11.md Change 1):
//   rows with 대상 → grid (top-level mark bar = grouped_bar, line = multi_line) · 비중 → pie · 시점 → line
//   · layer[1].transform → timeline · otherwise bar (horizontal when layer[0].encoding.y is 이름).

export type Point = { label: string; value: number | null; display: string; highlight: boolean };

export type ChartView =
  | { kind: "bar"; title: string; unitTitle: string | null; horizontal: boolean; points: Point[] }
  | { kind: "timeline" | "line"; title: string; unitTitle: string | null; points: Point[] }
  | { kind: "pie"; title: string; points: Point[] }
  | { kind: "grouped_bar" | "multi_line"; title: string; unitTitle: string | null; periods: string[]; series: Series[] }
  | { kind: "table"; title: string; columns: string[]; rows: string[][] }
  | { kind: "none"; title: string };

// One target of a grid chart, one point per period (null where the target has no row for that period)
export type Series = { name: string; points: (Point | null)[] };

type Row = Record<string, unknown>;
type Encoding = Record<string, { field?: string; title?: string | null; sort?: unknown } | undefined>;
type Spec = {
  title?: unknown;
  data?: { values?: unknown };
  mark?: unknown;
  encoding?: Encoding;
  layer?: { mark?: unknown; encoding?: Encoding; transform?: unknown }[];
};

export function toChartView(input: Record<string, unknown>): ChartView {
  const spec = input as Spec;
  const title = typeof spec.title === "string" ? spec.title : "";
  const values = spec.data?.values;
  if (!Array.isArray(values) || values.length === 0 || !values.every(isRow)) return { kind: "none", title };
  const rows = values as Row[];
  const has = (field: string) => rows.some((r) => field in r);
  const first = spec.layer?.[0]?.encoding ?? spec.encoding ?? {};

  if (has("대상")) {
    const mark = markType(spec.mark);
    if (mark !== "bar" && mark !== "line") return table(title, rows);
    const ordered = mark === "line" && spec.encoding?.x?.sort === null ? firstSeen(rows.map((r) => str(r["시점"]))) : sortedPeriods(rows);
    const names = firstSeen(rows.map((r) => str(r["대상"])));
    const series = names.map((name) => ({
      name,
      points: ordered.map((period) => {
        const row = rows.find((r) => str(r["대상"]) === name && str(r["시점"]) === period);
        return row ? point(row, period) : null;
      }),
    }));
    return { kind: mark === "bar" ? "grouped_bar" : "multi_line", title, unitTitle: titleOf(spec.encoding?.y), periods: ordered, series };
  }
  if (!rows.every((r) => "이름" in r && "값" in r)) return table(title, rows);
  if (has("비중")) {
    const ordered = [...rows].sort((a, b) => num(a["순서"]) - num(b["순서"]));
    return { kind: "pie", title, points: ordered.map((r) => point(r, str(r["이름"]))) };
  }
  if (has("시점")) {
    const ordered = [...rows].sort((a, b) => compareText(str(a["시점"]), str(b["시점"]))); // ordinal axis, no sort → ascending
    return { kind: "line", title, unitTitle: titleOf(first.y), points: ordered.map((r) => point(r, str(r["시점"]))) };
  }
  if (!spec.layer?.length || markType(spec.layer[0].mark) !== "bar") return table(title, rows);
  if (spec.layer[1]?.transform) {
    return { kind: "timeline", title, unitTitle: titleOf(first.y), points: rows.map((r) => point(r, str(r["이름"]))) };
  }
  const horizontal = first.y?.field === "이름";
  const nameAxis = horizontal ? first.y : first.x;
  const ordered = sortRows(rows, nameAxis?.sort, first);
  return { kind: "bar", title, unitTitle: titleOf(horizontal ? first.x : first.y), horizontal, points: ordered.map((r) => point(r, str(r["이름"]))) };
}

// Vega-Lite `sort` on the name axis: null keeps the data order, a list orders by name, "x" / "-y" sorts by the
// field encoded on that channel (ascending / descending).
function sortRows(rows: Row[], sort: unknown, encoding: Encoding): Row[] {
  if (sort === null) return rows;
  if (Array.isArray(sort)) {
    const at = (r: Row) => {
      const i = sort.indexOf(str(r["이름"]));
      return i < 0 ? sort.length : i;
    };
    return [...rows].sort((a, b) => at(a) - at(b));
  }
  if (typeof sort === "string" && /^-?[xy]$/.test(sort)) {
    const field = encoding[sort.replace("-", "")]?.field ?? "이름";
    const sign = sort.startsWith("-") ? -1 : 1;
    const missing = (r: Row) => r[field] == null;
    // rows without a value (symbol cells) stay last in either direction
    return [...rows].sort((a, b) => Number(missing(a)) - Number(missing(b)) || sign * compareValues(a[field], b[field]));
  }
  return [...rows].sort((a, b) => compareText(str(a["이름"]), str(b["이름"])));
}

function point(row: Row, label: string): Point {
  const value = typeof row["값"] === "number" ? row["값"] : null;
  const display = typeof row["표시"] === "string" ? row["표시"] : value === null ? "" : String(value);
  return { label, value, display, highlight: row["강조"] === "예" };
}

// Fallback: the rows themselves, with the label columns and the display string the backend wrote
function table(title: string, rows: Row[]): ChartView {
  if (typeof console !== "undefined") console.warn("chart: unrecognised Vega-Lite spec, showing its rows as a table");
  const has = (f: string) => rows.some((r) => f in r);
  const columns = ["대상", "시점", "이름"].filter(has).concat(has("표시") ? ["표시"] : has("값") ? ["값"] : []);
  if (!columns.length) return { kind: "none", title };
  return { kind: "table", title, columns: columns.map((c) => (c === "표시" ? "값" : c)), rows: rows.map((r) => columns.map((c) => str(r[c]))) };
}

function sortedPeriods(rows: Row[]): string[] {
  return firstSeen(rows.map((r) => str(r["시점"]))).sort(compareText);
}

function firstSeen(items: string[]): string[] {
  return [...new Set(items)];
}

function markType(mark: unknown): string | null {
  if (typeof mark === "string") return mark;
  if (mark && typeof mark === "object" && typeof (mark as { type?: unknown }).type === "string") return (mark as { type: string }).type;
  return null;
}

function titleOf(axis: { title?: string | null } | undefined): string | null {
  return typeof axis?.title === "string" ? axis.title : null;
}

function isRow(v: unknown): v is Row {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown): string {
  return v == null ? "" : String(v);
}

function num(v: unknown): number {
  return typeof v === "number" ? v : Number.MAX_SAFE_INTEGER;
}

function compareText(a: string, b: string): number {
  return a.localeCompare(b, "ko", { numeric: true });
}

function compareValues(a: unknown, b: unknown): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return compareText(str(a), str(b));
}
