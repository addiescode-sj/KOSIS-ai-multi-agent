// ChatResult payloads shaped like the backend's `result` event (chat_result in src/kosis_agent/graph/state.py),
// built from the same objects tests/test_workflow.py feeds the graph (answered / refused / clarify), plus the
// multi-question and chart-only turns. Used by the lib tests.

const table = {
  key: "101_DT_A", org_id: "101", tbl_id: "DT_A", table_name: "표 DT_A", survey_name: "조사",
  organization: "국가데이터처", period: "년 (2015 ~ 2025)", url: "https://kosis.kr", score: null,
};
const source = { org_id: "101", tbl_id: "DT_A", survey_nm: "조사", tbl_nm: "표 DT_A", url: null };
const base = {
  question: "2024년 출생아 수", route: "found", candidates: [table], picked: [table], answer_origin: "stat",
  table_key: "101_DT_A", chart: null, attempted_keys: ["101_DT_A"], reused_table: false, degraded: false,
  resolved_question: null, interpreted_by: "rule", chart_type: null, chart_note: null, sub_results: null, combined: null,
};

export const ANSWERED = {
  ...base,
  no_answer: false,
  no_answer_reason: null,
  outcome: "answered",
  answer: "101_DT_A: 100명\n\n출처: KOSIS / 조사 / 표 DT_A",
  chart: null as Record<string, unknown> | null, // set below to the phase 1 line spec
  stat_answer: {
    outcome: "answered", query: null, text: "101_DT_A: 100명\n\n출처: KOSIS / 조사 / 표 DT_A",
    result: { facts: [{ id: "f1", value: 100.0, unit: "명", period: "2024", dims: null, source_ref: "DT_A|T1|11|2024",
      raw: null, derivation: null, round: null, measure_kind: null, note: null }] },
    parts: [{ kind: "fact", fact_id: "f1" }], refusal: null, clarify_options: [], clarify_choices: [], default_applied: {},
    source, numbers_origin: null, numbers_verified: false, defaults_verified: false, chart: null, caveats: [],
    context_fact_ids: [], table: null,
  },
};

export const REFUSED = {
  ...base,
  question: "2030년 출생아 수",
  no_answer: true,
  no_answer_reason: "no_period",
  outcome: "refused",
  answer: "101_DT_A: 거절 (no_period)",
  stat_answer: {
    outcome: "refused", text: "101_DT_A: 거절 (no_period)", result: { facts: [] }, parts: [],
    refusal: { reason: "no_period", message: "101_DT_A: 거절 (no_period)", available: null },
    clarify_options: [], source, caveats: [],
  },
};

export const CLARIFY = {
  ...base,
  question: "출생아 수",
  no_answer: true,
  no_answer_reason: null,
  outcome: "clarify",
  answer: "어느 지역 기준인가요?",
  stat_answer: { outcome: "clarify", text: "어느 지역 기준인가요?", result: { facts: [] }, parts: [], refusal: null,
    clarify_options: ["서울", "부산"], source, caveats: [] },
};

// Phase 2 gate refused: phase 1 never ran, no stat_answer
export const SEARCH_REFUSED = {
  ...base,
  question: "비트코인 가격", route: "refuse", candidates: [], picked: [], table_key: null, attempted_keys: [],
  no_answer: true, no_answer_reason: "out_of_scope", outcome: "refused", answer: "범위 밖이에요.",
  answer_origin: "refusal", stat_answer: null,
};

export const PARTIAL = {
  ...base,
  question: "2023년 출생아 수와 2030년 사망자 수",
  no_answer: false,
  outcome: "answered",
  answer_origin: "multi",
  answer: "2023년 출생아 수는 230,028명입니다.\n\n2030년 자료는 없습니다.",
  stat_answer: null,
  sub_results: [
    { question: "2023년 출생아 수", table_key: "101_DT_A", outcome: "answered", reason: null, text: "2023년 출생아 수는 230,028명입니다.", answer: null },
    { question: "2030년 사망자 수", table_key: "101_DT_B", outcome: "refused", reason: "no_period", text: "2030년 자료는 없습니다.", answer: null },
  ],
};

// A chart-only turn whose chart could not be drawn: failed, the note is the whole answer
export const CHART_FAILED = {
  ...base,
  no_answer: true, outcome: "failed", answer: "원 그래프는 비율 자료에만 그릴 수 있어요.", answer_origin: "chart",
  chart_note: "원 그래프는 비율 자료에만 그릴 수 있어요.", stat_answer: null,
};

// ---------------------------------------------------------------- phase 1 Vega-Lite specs (T3-11 Change 1)
// Built the way kosis_chat/chart/vega.py `to_vega` builds them at the pinned commit 907b703 (_bars, _timeline,
// _draw_line, _draw_pie, _draw_grid): same field names, layers, encodings and sorts. Values are test values.

const SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json";
const HIGHLIGHT = "#2f6db5", OTHER = "#b8c4d6";
const vlBase = (title: string, values: Record<string, unknown>[]) => ({
  $schema: SCHEMA, title, width: 360, data: { values },
  config: { font: "sans-serif", axis: { labelFontSize: 11, titleFontSize: 11 } },
});
const color = { field: "강조", type: "nominal", legend: null, scale: { domain: ["예", "아니오"], range: [HIGHLIGHT, OTHER] } };
const tooltip = [{ field: "이름", type: "nominal" }, { field: "표시", type: "nominal", title: "값" }];
const row = (이름: string, 값: number | null, 표시: string, 강조 = false) => ({ 이름, 값, 표시, 강조: 강조 ? "예" : "아니오" });

function bars(title: string, rows: Record<string, unknown>[], unit: string, sort: unknown = "-x", horizontal = true) {
  const value = { field: "값", type: "quantitative", title: `값 (${unit})` };
  const name = { field: "이름", type: "nominal", title: null, sort, axis: { labelAngle: 0, labelLimit: 160 } };
  const enc = horizontal ? { y: name, x: value } : { x: name, y: value };
  return {
    ...vlBase(title, rows),
    layer: [
      { mark: { type: "bar" }, encoding: { ...enc, color, tooltip } },
      { mark: horizontal ? { type: "text", align: "left", dx: 3 } : { type: "text", dy: -6 }, encoding: { ...enc, text: { field: "표시" } } },
    ],
    ...(horizontal ? { height: { step: 22 } } : {}),
  };
}

const BAR_ROWS = [row("세종", 0.97, "0.97명"), row("서울", 0.55, "0.55명", true), row("부산", 0.66, "0.66명")];

export const VEGA = {
  bar: bars("2023년 시도별 합계출산율", BAR_ROWS, "명"),
  barVertical: bars("2023년 시도별 합계출산율", BAR_ROWS, "명", ["서울", "부산", "세종"], false),
  timeline: (() => {
    const rows = [row("2021", 260562, "260,562명"), row("2022", 249186, "249,186명"), row("2023", 230028, "230,028명", true)];
    const x = { field: "이름", type: "ordinal", title: null, sort: null, axis: { labelAngle: 0 } };
    const y = { field: "값", type: "quantitative", title: "값 (명)" };
    return {
      ...vlBase("출생아 수", rows),
      layer: [
        { mark: { type: "bar" }, encoding: { x, y, color, tooltip } },
        { transform: [{ filter: "datum['강조'] == '예'" }], mark: { type: "text", dy: -6 }, encoding: { x, y, text: { field: "표시" } } },
      ],
      width: 360,
    };
  })(),
  line: (() => {
    // rows out of period order on purpose: the ordinal axis sorts them ascending
    const rows = [
      { 시점: "2023", ...row("2023", 0.72, "0.72명", true) },
      { 시점: "2021", ...row("2021", 0.81, "0.81명") },
      { 시점: "2022", ...row("2022", 0.78, "0.78명") },
    ];
    const x = { field: "시점", type: "ordinal", title: null, axis: { labelAngle: 0 } };
    const y = { field: "값", type: "quantitative", title: "값 (명)", scale: { zero: false } };
    return {
      ...vlBase("합계출산율 추이", rows),
      layer: [
        { mark: { type: "line", color: OTHER }, encoding: { x, y } },
        { mark: { type: "point", filled: true, size: 60 }, encoding: { x, y, color, tooltip } },
        { transform: [{ filter: "datum['강조'] == '예'" }], mark: { type: "text", dy: -10 }, encoding: { x, y, text: { field: "표시" } } },
      ],
    };
  })(),
  pie: (() => {
    const rows = [
      { ...row("1인 가구", 7.5, "750만 가구", true), 비중: 35.5, 순서: 0 },
      { ...row("그 외", 13.6, "1,360만 가구"), 비중: 64.5, 순서: 1 },
    ];
    const spec: Record<string, unknown> = vlBase("1인 가구 비중", rows);
    delete spec.width; // _draw_pie pops the width
    return {
      ...spec,
      layer: [{ mark: { type: "arc", innerRadius: 50, stroke: "white" }, encoding: {
        theta: { field: "값", type: "quantitative", stack: true },
        color: { field: "이름", type: "nominal", title: null, sort: ["1인 가구", "그 외"], scale: { range: [HIGHLIGHT, OTHER] } },
        order: { field: "순서", type: "quantitative" },
        tooltip: [...tooltip, { field: "비중", type: "quantitative", title: "비중(%)" }],
      } }],
    };
  })(),
  ...(() => {
    const series = [["서울", [["2022", 0.59, "0.59명"], ["2023", 0.55, "0.55명"]]], ["부산", [["2022", 0.72, "0.72명"], ["2023", 0.66, "0.66명"]]]] as const;
    const rows = series.flatMap(([name, pts]) => pts.map(([period, v, d]) => ({ 대상: name, 시점: period, ...row(period, v, d), 이름: name })));
    const y = { field: "값", type: "quantitative", title: "값 (명)" };
    const colorBy = { field: "대상", type: "nominal", title: null };
    const tip = [{ field: "대상", type: "nominal" }, { field: "시점", type: "ordinal" }, { field: "표시", type: "nominal", title: "값" }];
    return {
      grouped_bar: { ...vlBase("시도별 합계출산율", rows), mark: { type: "bar" }, encoding: {
        x: { field: "시점", type: "ordinal", title: null, axis: { labelAngle: 0 } }, xOffset: { field: "대상" }, y, color: colorBy, tooltip: tip } },
      multi_line: { ...vlBase("시도별 합계출산율", rows), mark: { type: "line", point: true }, encoding: {
        x: { field: "시점", type: "ordinal", title: null, sort: null }, y: { ...y, scale: { zero: false } }, color: colorBy, tooltip: tip } },
    };
  })(),
};

ANSWERED.chart = VEGA.line;
