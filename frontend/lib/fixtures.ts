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
  chart: { $schema: "https://vega.github.io/schema/vega-lite/v5.json", mark: "bar", data: { values: [{ period: "2024", value: 100 }] } },
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
