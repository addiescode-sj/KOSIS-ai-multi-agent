// View model Recharts draws, built from a phase 1 Vega-Lite spec by lib/chat/chart (T3-11 Change 1).

export type Point = { label: string; value: number | null; display: string; highlight: boolean };

export type ChartView =
  | { kind: "bar"; title: string; unitTitle: string | null; horizontal: boolean; points: Point[] }
  | { kind: "timeline" | "line"; title: string; unitTitle: string | null; points: Point[] }
  | { kind: "pie"; title: string; points: Point[] }
  | { kind: "grouped_bar" | "multi_line"; title: string; unitTitle: string | null; periods: string[]; series: Series[] }
  // composition change (T3-02): per period one 100% bar, series = the part, then its rest ("그 외")
  | { kind: "stacked_bar"; title: string; unitTitle: string | null; periods: string[]; series: Series[] }
  | { kind: "table"; title: string; columns: string[]; rows: string[][] }
  | { kind: "none"; title: string };

// One target of a grid chart, one point per period (null where the target has no row for that period)
export type Series = { name: string; points: (Point | null)[] };
