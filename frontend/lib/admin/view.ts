import type { AdminMetrics, Summary } from "./schemas.ts";

// GET /admin/api/metrics → what the dashboard cards draw. The numbers are the operator app's (log_metrics) as they
// are; only display arithmetic happens here (ratio → %, change against the range before, a share of a distribution).
// A card with no stored source is null and the screen says "데이터 없음" — never a made-up number.

export type Labeled = { label: string; count: number };

export type StatKey = "questions" | "refusal_rate" | "accuracy" | "thumbs_up_rate";
export type StatView = {
  key: StatKey;
  label: string;
  value: number | null; // null: no stored source
  unit: "건" | "%";
  delta: number | null; // null: no range before (past the 90-day limit) or no value
  deltaUnit: "건" | "%p";
  good: boolean | null; // is the change good (the color)
  note: string;
};

export type DashboardView = {
  range: { start: string; end: string };
  notice: string | null;
  readAt: string;
  stats: StatView[];
  answerTypes: Labeled[];
  refusals: { code: string; label: string; count: number }[];
  upReasons: Labeled[];
  downReasons: Labeled[];
  daily: { day: string; count: number }[];
  hourly: number[][] | null; // not stored
  rates: {
    citationClick: { value: number | null; note: string };
    multiTurn: { value: number | null; note: string };
    dropBeforeAnswer: { value: number | null; note: string };
    copyAnswer: { value: number | null; note: string };
    retry: { value: number | null; note: string };
  };
  tables: { key: string; survey: string; uses: number }[];
  latency: { p50: number | null; p95: number | null } | null; // whole answer only: per stage is Prometheus
  ttft: { p50: number | null; p95: number | null } | null;
  llm: { calls: number | null; inputTokens: number | null; outputTokens: number | null; costUsd: null } | null;
  // screen events dropped at intake, by reason (ingest_rejects). Server write failures live in the backend process.
  logQuality: { reason: string; eventType: string; count: number }[];
};

const pct = (ratio: number | null | undefined) => (ratio == null ? null : Math.round(ratio * 1000) / 10);
const sub = (a: number | null, b: number | null) => (a == null || b == null ? null : Math.round((a - b) * 10) / 10);

function summaryNumbers(s: Summary) {
  const refused = s.outcome_ratio.find((r) => r.outcome === "refused");
  return {
    questions: s.question_count[0]?.n ?? 0,
    refusal: s.outcome_ratio.length ? pct(refused?.share ?? 0) : null, // no questions: no rate
    thumbsUp: pct(s.thumbs_up_ratio[0]?.ratio),
  };
}

export function toView(m: AdminMetrics): DashboardView {
  const name = (kind: string, code: string | null) => (code == null ? "(없음)" : (m.labels[kind]?.[code] ?? code));
  const now = summaryNumbers(m.tabs.summary.rows);
  const before = m.previous ? summaryNumbers(m.previous) : null;
  const dq = before ? now.questions - before.questions : null;
  const dr = before ? sub(now.refusal, before.refusal) : null;
  const dt = before ? sub(now.thumbsUp, before.thumbsUp) : null;
  const ups = m.tabs.summary.rows.thumbs_up_ratio[0];
  const usage = m.tabs.usage.rows;
  const cite = usage.citation_click_rate[0];
  const abort = usage.abort_rate[0];
  const copy = usage.copy_rate[0];
  const retry = usage.retry_rate[0];
  const n = (v: number) => v.toLocaleString("ko-KR");
  const convs = usage.turns_per_conversation.reduce((a, r) => a + r.n, 0);
  const multi = usage.turns_per_conversation.filter((r) => r.turns !== "1").reduce((a, r) => a + r.n, 0);
  const llm = m.tabs.cost.rows.llm_usage[0];
  const ttft = m.first_token[0];
  const vs = before ? "직전 같은 기간 대비" : "직전 기간 없음(90일 밖)";
  return {
    range: { start: m.filter.start, end: m.filter.end },
    notice: m.notice,
    readAt: m.read_at,
    stats: [
      { key: "questions", label: "총 질문 수", value: now.questions, unit: "건", delta: dq, deltaUnit: "건", good: dq == null ? null : dq >= 0, note: vs },
      { key: "refusal_rate", label: "거절률", value: now.refusal, unit: "%", delta: dr, deltaUnit: "%p", good: dr == null ? null : dr <= 0, note: vs },
      { key: "accuracy", label: "최종 수치 정확도", value: null, unit: "%", delta: null, deltaUnit: "%p", good: null, note: "골든셋 평가 결과는 DB에 없음" },
      { key: "thumbs_up_rate", label: "👍 비율", value: now.thumbsUp, unit: "%", delta: dt, deltaUnit: "%p", good: dt == null ? null : dt >= 0,
        note: ups ? `피드백 ${(ups.up + ups.down).toLocaleString("ko-KR")}건 중` : vs },
    ],
    answerTypes: m.tabs.summary.rows.outcome_ratio.map((r) => ({ label: name("outcome", r.outcome), count: r.n })),
    refusals: m.tabs.quality.rows.refusal_reasons.map((r) => ({ code: r.reason_code ?? "-", label: name("reason", r.reason_code), count: r.n })),
    upReasons: m.tabs.quality.rows.up_reasons.map((r) => ({ label: name("up", r.choice), count: r.n })),
    downReasons: m.tabs.quality.rows.down_reasons.map((r) => ({ label: name("down", r.choice), count: r.n })),
    daily: m.tabs.summary.daily.question_count.map((d) => ({ day: d.day, count: d.n ?? 0 })),
    hourly: null,
    rates: {
      citationClick: { value: pct(cite?.rate), note: cite ? `숫자 답 ${cite.with_numbers.toLocaleString("ko-KR")}건 중 원표 클릭` : "숫자 답 없음" },
      multiTurn: { value: convs ? Math.round((multi / convs) * 1000) / 10 : null, note: `대화 ${convs.toLocaleString("ko-KR")}개 중 2턴 이상` },
      dropBeforeAnswer: { value: pct(abort?.rate), note: abort ? `질문 ${abort.questions.toLocaleString("ko-KR")}건 중 답 전 이탈` : "질문 없음" },
      copyAnswer: { value: pct(copy?.rate), note: copy?.answered ? `답한 질문 ${n(copy.answered)}건 중 복사` : "답한 질문 없음" },
      retry: {
        value: pct(retry?.rate),
        // the rate is the user's 다시 시도 only; the proxy's own reconnects are shown apart (T3-68 보강 1)
        note: retry?.questions
          ? `오류 후 ${n(retry.after_failure)} · 다시 받기 ${n(retry.regenerate)} · 원래 턴 미상 ${n(retry.unknown_origin)} · 자동 재연결 ${n(retry.auto_reconnect)}(비율 밖)`
          : "질문 없음",
      },
    },
    tables: usage.top_tables.map((t) => ({ key: t.picked_key, survey: t.survey_name ?? "-", uses: t.n })),
    latency: m.tabs.summary.rows.latency[0] ? { p50: m.tabs.summary.rows.latency[0].p50, p95: m.tabs.summary.rows.latency[0].p95 } : null,
    ttft: ttft ? { p50: ttft.p50, p95: ttft.p95 } : null,
    llm: llm ? { calls: llm.calls, inputTokens: llm.input_tokens, outputTokens: llm.output_tokens, costUsd: null } : null,
    logQuality: m.tabs.cost.rows.log_quality.map((r) => ({ reason: r.reason, eventType: r.event_type || "-", count: r.n })),
  };
}
