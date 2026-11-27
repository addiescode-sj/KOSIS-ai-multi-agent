import type { AdminDashboard, AdminFilter, AdminTrace } from "./schemas.ts";

// Sample numbers of the admin preview (the design mockup's values, T3-63). Not real metrics: every screen that
// shows them carries the "예시 데이터" badge. Served only by app/api/preview/admin and the preview pages.

export function sampleDashboard(filter: AdminFilter): AdminDashboard {
  return {
    filter,
    batch: { ok: true, updatedAt: "14:00" },
    stats: [
      { key: "questions", label: "총 질문 수", value: 2841, unit: "건", delta: 128, deltaUnit: "건", good: true, note: "오늘" },
      { key: "refusal_rate", label: "거절률", value: 6.4, unit: "%", delta: -1.2, deltaUnit: "%p", good: true, note: "목표 8% 미만" },
      { key: "accuracy", label: "최종 수치 정확도", value: 97.8, unit: "%", delta: 0.4, deltaUnit: "%p", good: true, note: "골든셋 기준" },
      { key: "thumbs_up_rate", label: "👍 비율", value: 82.1, unit: "%", delta: -0.8, deltaUnit: "%p", good: false, note: "피드백 1,204건 중" },
    ],
    answerTypes: [
      { label: "숫자", count: 1820 }, { label: "부분", count: 302 }, { label: "일반", count: 241 },
      { label: "거절", count: 182 }, { label: "되묻기", count: 214 }, { label: "오류", count: 82 },
    ],
    refusals: [
      { code: "YEAR_NOT_FOUND", label: "없는 연도", count: 64 },
      { code: "PERIOD_MISMATCH", label: "기간 불일치", count: 48 },
      { code: "CLASS_MISMATCH", label: "분류 불일치", count: 30 },
      { code: "UNIT_MISMATCH", label: "단위 다름", count: 22 },
      { code: "MISSING_DATA", label: "결측", count: 18 },
    ],
    upReasons: [
      { label: "수치가 정확함", count: 412 }, { label: "원하는 표를 찾음", count: 288 },
      { label: "설명이 이해하기 쉬움", count: 164 }, { label: "차트가 도움이 됨", count: 98 }, { label: "기타", count: 26 },
    ],
    downReasons: [
      { label: "표가 다름", count: 94 }, { label: "수치가 틀림", count: 52 }, { label: "이해하기 어려움", count: 48 }, { label: "기타", count: 22 },
    ],
    daily: [1820, 2140, 1960, 2410, 2280, 2690, 2520, 2841].map((count, i) => ({ day: `2026-11-${12 + i}`, count })),
    hourly: [
      [1, 3, 4, 4, 5, 5, 5, 5], [2, 3, 3, 4, 5, 5, 5, 5], [2, 2, 4, 5, 5, 5, 5, 4], [1, 3, 4, 4, 5, 5, 5, 5],
      [2, 3, 3, 4, 5, 5, 5, 5], [0, 0, 2, 3, 3, 3, 4, 2], [0, 1, 2, 2, 3, 4, 3, 3],
    ],
    rates: { citationClick: 34.2, turnsPerChat: 2.6, turnsPerChatPrev: 2.4, dropBeforeAnswer: 4.1 },
    tables: [
      { name: "합계출산율", survey: "인구동향조사", uses: 642, lastUsed: "2026-11-19" },
      { name: "출생아 수", survey: "인구동향조사", uses: 518, lastUsed: "2026-11-19" },
      { name: "사망자 수", survey: "인구동향조사", uses: 304, lastUsed: "2026-11-19" },
      { name: "연령별 인구 현황", survey: "인구총조사", uses: 221, lastUsed: "2026-11-18" },
      { name: "혼인 건수", survey: "인구동향조사", uses: 186, lastUsed: "2026-11-18" },
      { name: "소비자물가지수", survey: "소비자물가조사", uses: 142, lastUsed: "2026-11-17" },
    ],
    latency: [
      { stage: "질문 이해", p50: 820, p95: 1640 }, { stage: "표 찾기", p50: 2310, p95: 5120 },
      { stage: "계산", p50: 410, p95: 980 }, { stage: "차트 생성", p50: 640, p95: 1420 },
    ],
    ttft: { seconds: [3.2, 2.9, 3.4, 3.1, 2.7, 2.8, 2.5, 2.6], p50: 2640, p95: 7980 },
    llm: { calls: 3.4, inputTokens: 4210, outputTokens: 612, costUsd: 0.018 },
    logQuality: [
      { label: "검사에서 걸린 이벤트 수", value: 12, unit: "건", ok: true },
      { label: "짝 없는 질문 비율", value: 0.3, unit: "%", ok: true },
      { label: "로그 전송 실패 수", value: 0, unit: "건", ok: true },
    ],
  };
}

export function sampleTrace(requestId: string): AdminTrace {
  return {
    requestId,
    spans: [
      { name: "graph.run", start: 0, end: 100 }, { name: "understand_question", start: 0, end: 14 },
      { name: "search_tables", start: 14, end: 52 }, { name: "compute_value", start: 52, end: 68 },
      { name: "render_chart", start: 68, end: 92 }, { name: "stream_answer", start: 88, end: 100 },
    ],
  };
}

export const SAMPLE_TRACE_ID = "7f3a9c21e04b";
