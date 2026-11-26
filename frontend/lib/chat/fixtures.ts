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
  chart: null as Record<string, unknown> | null, // set below to the line chart data
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

// ---------------------------------------------------------------- chart data (ChartSpec JSON, T3-02 / KOS-202)
// Shaped like kosis_chat/chart/module.py `chart_data` (phase 1 T1-32): kind, title, unit, series[].points[] with the
// display strings and highlights the backend wrote, fact_ids, bar orientation and sort. Values are test values.

type Pt = { label: string; value: number | null; display: string; fact_id?: string | null; period?: string | null; highlight?: boolean; share_pct?: number | null };
const pt = (label: string, value: number | null, display: string, highlight = false, extra: Partial<Pt> = {}): Pt => ({
  label, value, display, fact_id: null, period: null, highlight, share_pct: null, ...extra,
});
const spec = (kind: string, title: string, unit: string | null, series: [string, Pt[]][], layout: Record<string, unknown> = {}) => ({
  kind, title, unit, series: series.map(([name, points]) => ({ name, points })), fact_ids: ["f1"],
  orientation: null, sort: null, pattern: null, ...layout,
});

const BARS = [pt("세종", 0.97, "0.97명"), pt("서울", 0.55, "0.55명", true), pt("부산", 0.66, "0.66명")];
const GRID: [string, Pt[]][] = [
  ["서울", [pt("2022", 0.59, "0.59명", false, { period: "2022" }), pt("2023", 0.55, "0.55명", false, { period: "2023" })]],
  ["부산", [pt("2022", 0.72, "0.72명", false, { period: "2022" }), pt("2023", 0.66, "0.66명", false, { period: "2023" })]],
];

export const CHARTS = {
  bar: spec("bar", "2023년 시도별 합계출산율", "명", [["s", BARS]], { orientation: "horizontal", sort: "-x" }),
  barVertical: spec("bar", "2023년 시도별 합계출산율", "명", [["s", BARS]], { orientation: "vertical", sort: ["서울", "부산", "세종"] }),
  timeline: spec("timeline", "출생아 수", "명", [["s", [pt("2021", 260562, "260,562명"), pt("2022", 249186, "249,186명"),
    pt("2023", 230028, "230,028명", true)]]]),
  // points out of period order on purpose: a line is drawn by period ascending
  line: spec("line", "합계출산율 추이", "명", [["s", [pt("2023", 0.72, "0.72명", true, { period: "2023" }),
    pt("2021", 0.81, "0.81명", false, { period: "2021" }), pt("2022", 0.78, "0.78명", false, { period: "2022" })]]]),
  pie: spec("pie", "1인 가구 비중", "만 가구", [["s", [pt("1인 가구", 7.5, "750만 가구", true, { share_pct: 35.5 }),
    pt("그 외", 13.6, "1,360만 가구", false, { share_pct: 64.5 })]]]),
  grouped_bar: spec("grouped_bar", "시도별 합계출산율", "명", GRID),
  multi_line: spec("multi_line", "시도별 합계출산율", "명", GRID),
  // a stacked bar: the share facts, then the rest to 100 the backend added as the second series (chart_data)
  stacked_bar: spec("stacked_bar", "서울 1인 가구 비중 비교", "%", [
    ["서울 1인 가구", [pt("2015년", 29.5, "29.5%", true, { period: "2015" }), pt("2023년", 39.3, "39.3%", false, { period: "2023" })]],
    ["그 외", [pt("2015년", 70.5, "70.5%", false, { period: "2015" }), pt("2023년", 60.7, "60.7%", false, { period: "2023" })]],
  ]),
};

ANSWERED.chart = CHARTS.line;
