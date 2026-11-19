"use client";

import { useMemo } from "react";
import {
  Bar, BarChart, CartesianGrid, LabelList, Legend, Line, LineChart, Pie, PieChart, Rectangle, ResponsiveContainer,
  Sector, Tooltip, XAxis, YAxis, type BarShapeProps, type PieSectorShapeProps, type TooltipContentProps,
} from "recharts";

import { toChartView } from "@/lib/chat/chart";
import type { ChartView, Point } from "@/types/chart";

// Draws ChatResult.chart (phase 1 Vega-Lite) with Recharts through lib/chart.ts (T3-11 Change 1). Bar heights,
// points and slices are the backend's values; labels and tooltips are its display strings (`표시`) as they are.
const HIGHLIGHT = "#2257e8"; // brand-600
const OTHER = "#bfd3fe"; // brand-200
const SERIES = ["#2257e8", "#93b4fd", "#1b45c4", "#3b74f6", "#dbe7fe"];
const AXIS = { stroke: "#e7eaf0", tick: { fill: "#9ca3af", fontSize: 12 }, tickLine: false } as const;

type Datum = Point & { mark: string }; // mark: the label shown on the chart ("" when not highlighted)

export function StatChart({ spec }: { spec: Record<string, unknown> }) {
  const view = useMemo(() => toChartView(spec), [spec]);
  return (
    <figure className="space-y-3">
      {(view.title || ("unitTitle" in view && view.unitTitle)) && (
        <figcaption className="flex items-baseline justify-between gap-3">
          <span className="text-sm font-semibold text-ink">{view.title}</span>
          {"unitTitle" in view && view.unitTitle && <span className="text-xs text-mute">{view.unitTitle}</span>}
        </figcaption>
      )}
      <Body view={view} />
    </figure>
  );
}

function Body({ view }: { view: ChartView }) {
  switch (view.kind) {
    case "bar":
      return view.horizontal ? <HorizontalBars points={view.points} /> : <ColumnBars points={view.points} labelAll />;
    case "timeline":
      return <ColumnBars points={view.points} />;
    case "line":
      return <PeriodLine points={view.points} />;
    case "pie":
      return <Donut points={view.points} />;
    case "grouped_bar":
    case "multi_line":
      return <Grid kind={view.kind} periods={view.periods} series={view.series} />;
    case "table":
      return <RowsTable columns={view.columns} rows={view.rows} />;
    case "none":
      return <p className="text-[13px] text-mute">이 차트는 화면에 그릴 수 없습니다.</p>;
  }
}

const data = (points: Point[], labelAll = false): Datum[] =>
  points.map((p) => ({ ...p, mark: labelAll || p.highlight ? p.display : "" }));

const colored = (props: BarShapeProps) => (
  <Rectangle {...props} fill={(props.payload as Datum).highlight ? HIGHLIGHT : OTHER} radius={4} />
);

function HorizontalBars({ points }: { points: Point[] }) {
  return (
    <ResponsiveContainer width="100%" height={points.length * 34 + 24}>
      <BarChart data={data(points, true)} layout="vertical" margin={{ top: 4, right: 72, bottom: 4, left: 4 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="label" width={96} {...AXIS} axisLine={false} />
        <Tooltip content={PointTooltip} cursor={{ fill: "#f4f6fa" }} />
        <Bar dataKey="value" shape={colored} isAnimationActive={false} barSize={18}>
          <LabelList dataKey="mark" position="right" fill="#6b7280" fontSize={12} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function ColumnBars({ points, labelAll = false }: { points: Point[]; labelAll?: boolean }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data(points, labelAll)} margin={{ top: 20, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke="#e7eaf0" strokeDasharray="4 4" />
        <XAxis dataKey="label" {...AXIS} interval={points.length > 12 ? "preserveStartEnd" : 0} />
        <YAxis {...AXIS} axisLine={false} width={56} />
        <Tooltip content={PointTooltip} cursor={{ fill: "#f4f6fa" }} />
        <Bar dataKey="value" shape={colored} isAnimationActive={false} maxBarSize={48}>
          <LabelList dataKey="mark" position="top" fill="#6b7280" fontSize={12} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function PeriodLine({ points }: { points: Point[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data(points)} margin={{ top: 24, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke="#e7eaf0" strokeDasharray="4 4" />
        <XAxis dataKey="label" {...AXIS} />
        <YAxis {...AXIS} axisLine={false} width={56} domain={["auto", "auto"]} />
        <Tooltip content={PointTooltip} />
        <Line
          dataKey="value"
          stroke={OTHER}
          strokeWidth={2}
          isAnimationActive={false}
          dot={(props) => {
            const d = props.payload as Datum;
            return <circle key={props.index} cx={props.cx} cy={props.cy} r={d.highlight ? 5 : 3.5} fill={d.highlight ? HIGHLIGHT : "#93b4fd"} />;
          }}
        >
          <LabelList dataKey="mark" position="top" fill="#111827" fontSize={12} fontWeight={600} />
        </Line>
      </LineChart>
    </ResponsiveContainer>
  );
}

function Donut({ points }: { points: Point[] }) {
  const colors = points.map((p, i) => (p.highlight ? SERIES[i % SERIES.length] : OTHER));
  return (
    <div className="flex flex-wrap items-center gap-6">
      <div className="size-48">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data(points)}
              dataKey="value"
              nameKey="label"
              innerRadius={50}
              outerRadius={88}
              stroke="#fff"
              isAnimationActive={false}
              shape={(props: PieSectorShapeProps) => <Sector {...props} fill={colors[props.index ?? 0]} />}
            />
            <Tooltip content={PointTooltip} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="space-y-1.5 text-[13px]">
        {points.map((p, i) => (
          <li key={p.label} className="flex items-center gap-2">
            <span className="size-2.5 rounded-sm" style={{ background: colors[i] }} />
            <span className="text-sub">{p.label}</span>
            <span className="font-medium">{p.display}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Grid({ kind, periods, series }: { kind: "grouped_bar" | "multi_line"; periods: string[]; series: { name: string; points: (Point | null)[] }[] }) {
  // Recharts wants one object per period: {label, <series>: value, "<series>__표시": display}
  const rows = periods.map((label, i) => ({
    label,
    ...Object.fromEntries(series.flatMap((s) => [[s.name, s.points[i]?.value ?? null], [`${s.name}__표시`, s.points[i]?.display ?? ""]])),
  }));
  const common = (
    <>
      <CartesianGrid vertical={false} stroke="#e7eaf0" strokeDasharray="4 4" />
      <XAxis dataKey="label" {...AXIS} />
      <YAxis {...AXIS} axisLine={false} width={56} domain={kind === "multi_line" ? ["auto", "auto"] : [0, "auto"]} />
      <Tooltip content={GridTooltip} cursor={{ fill: "#f4f6fa" }} />
      {/* the series order of the data (Recharts sorts the legend by name by default) */}
      <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, color: "#6b7280" }}
        itemSorter={(item) => series.findIndex((s) => s.name === item.dataKey)} />
    </>
  );
  return (
    <ResponsiveContainer width="100%" height={240}>
      {kind === "grouped_bar" ? (
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          {common}
          {series.map((s, i) => <Bar key={s.name} dataKey={s.name} fill={SERIES[i % SERIES.length]} radius={[4, 4, 0, 0]} isAnimationActive={false} maxBarSize={36} />)}
        </BarChart>
      ) : (
        <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          {common}
          {series.map((s, i) => <Line key={s.name} dataKey={s.name} stroke={SERIES[i % SERIES.length]} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} connectNulls={false} />)}
        </LineChart>
      )}
    </ResponsiveContainer>
  );
}

function RowsTable({ columns, rows }: { columns: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-line">
      <table className="w-full text-[13px]">
        <thead className="bg-canvas text-left text-xs text-sub">
          <tr>{columns.map((c) => <th key={c} className="px-3 py-2 font-medium">{c}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className="px-3 py-2 tabular-nums">{c}</td>)}</tr>)}
        </tbody>
      </table>
    </div>
  );
}

function TooltipBox({ title, lines }: { title: string; lines: [string, string][] }) {
  return (
    <div className="rounded-lg border border-line bg-white px-3 py-2 text-xs shadow-sm">
      <p className="font-medium text-ink">{title}</p>
      {lines.map(([k, v]) => (
        <p key={k} className="mt-0.5 text-sub">{k && <span className="mr-1.5">{k}</span>}<span className="font-medium text-ink">{v}</span></p>
      ))}
    </div>
  );
}

function PointTooltip({ active, payload }: TooltipContentProps) {
  const d = active ? (payload?.[0]?.payload as Datum | undefined) : undefined;
  return d ? <TooltipBox title={d.label} lines={[["", d.display]]} /> : null;
}

function GridTooltip({ active, payload, label }: TooltipContentProps) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload as Record<string, unknown>;
  return <TooltipBox title={String(label)} lines={payload.map((p) => [String(p.dataKey), String(row[`${String(p.dataKey)}__표시`] ?? "")])} />;
}
