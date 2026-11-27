import type { AnswerKind, Citation, Headline, Tone } from "../../types/answer.ts";
import type { ChatMessage } from "../../types/chat.ts";
import type { ChatResult, Fact } from "./schemas.ts";

// Pure view logic over a ChatResult. Nothing here makes a number: values come from phase 1 facts as they are,
// and every sentence shown is the backend's own text.

export function answerKind(result: ChatResult): AnswerKind {
  const outcome = result.outcome ?? (result.no_answer ? "refused" : "answered"); // records older than T3-23
  const subs = result.sub_results ?? [];
  if (outcome === "answered" && subs.some((s) => s.outcome !== "answered")) return "partial";
  return outcome;
}

// The first number the answer cites (its first fact part). Shown only when the cell has a value; a fact with a
// derivation carries its own rounding, which display keeps; a source cell is shown with all its digits.
export function headline(result: ChatResult): Headline | null {
  const stat = result.stat_answer;
  if (!stat || stat.outcome !== "answered") return null;
  const first = stat.parts.find((p) => p.kind === "fact");
  if (!first || first.kind !== "fact") return null;
  const fact = stat.result.facts.find((f) => f.id === first.fact_id);
  if (!fact || fact.value == null) return null;
  return { value: formatValue(fact), raw: fact.value, round: fact.round ?? null, unit: fact.unit ?? null, period: fact.period ?? null };
}

export function formatValue(fact: Pick<Fact, "value" | "round">): string {
  if (fact.value == null) return "";
  return fact.value.toLocaleString("ko-KR", {
    minimumFractionDigits: fact.round ?? 0,
    maximumFractionDigits: fact.round ?? 20,
  });
}

// 출처: KOSIS / 조사명 / 통계표명 (+링크). The table phase 1 answered from first, else the table search picked.
// key and rank name the table for the citation_click event (log props table_key, rank: 1-based among the picked
// tables, else among the candidates; null when the table is in neither list).
export function citation(result: ChatResult): Citation | null {
  const source = result.stat_answer?.source;
  const picked = result.picked.find((t) => t.key === result.table_key) ?? result.picked[0];
  if (source) {
    const url = source.url ?? (picked && picked.tbl_id === source.tbl_id ? picked.url : null);
    const key = `${source.org_id}_${source.tbl_id}`;
    return { survey: source.survey_nm, table: source.tbl_nm, url, text: `KOSIS / ${source.survey_nm} / ${source.tbl_nm}`,
      key, rank: rankOf(result, key) };
  }
  if (picked && result.outcome !== "refused") {
    return { survey: picked.survey_name, table: picked.table_name, url: picked.url,
      text: `KOSIS / ${picked.survey_name} / ${picked.table_name}`, key: picked.key, rank: rankOf(result, picked.key) };
  }
  return null;
}

function rankOf(result: ChatResult, key: string): number | null {
  for (const list of [result.picked, result.candidates]) {
    const i = list.findIndex((t) => t.key === key);
    if (i >= 0) return i + 1;
  }
  return null;
}

// What the 복사 button puts on the clipboard: the backend's answer as it is, with its 출처 line (added only when the
// answer text does not carry it already). Nothing is reworded.
export function copyText(result: ChatResult): string {
  const c = citation(result);
  const answer = result.answer.trim();
  if (!c || answer.includes(c.text)) return answer;
  return `${answer}\n\n출처: ${c.text}${c.url ? ` (${c.url})` : ""}`;
}

// The request_id of one assistant turn: the result's own (backend T3-55), else the stream's meta (trace_id).
// Restored turns have only the result. null for a turn the backend never answered.
export function requestIdOf(message: Pick<ChatMessage, "parts">): string | null {
  let fromMeta: string | null = null;
  for (const p of message.parts) {
    if (p.type === "data-result") {
      if (p.data.request_id) return p.data.request_id;
    } else if (p.type === "data-meta") fromMeta = p.data.requestId ?? null;
  }
  return fromMeta;
}

// The backend thread of one streamed turn (its meta); null for a restored turn (the conversation's own then).
export function threadIdOf(message: Pick<ChatMessage, "parts">): string | null {
  const meta = message.parts.find((p) => p.type === "data-meta");
  return meta?.type === "data-meta" ? meta.data.threadId : null;
}

// Where an assistant turn stands while (or after) it streams, for the generating card.
export enum GeneratingStatus {
  WORKING = "WORKING",
  RECONNECTING = "RECONNECTING",
  STOPPED = "STOPPED",
  INCOMPLETE = "INCOMPLETE",
}

// The heading the generating card shows for a status
export interface GeneratingUserMessage {
  title: string;
  text: string;
}

export function turnState(message: Pick<ChatMessage, "parts">, running: boolean): GeneratingStatus {
  const reconnecting = message.parts.some((p) => p.type === "data-reconnect" && !p.data.connected);
  const draft = message.parts.some((p) => p.type === "data-draft" && p.data.text);
  if (running) return reconnecting ? GeneratingStatus.RECONNECTING : GeneratingStatus.WORKING;
  // tokens already shown stay, marked unfinished (api.md 2 error)
  return draft ? GeneratingStatus.INCOMPLETE : GeneratingStatus.STOPPED;
}

export function refusalReason(result: ChatResult): string | null {
  return result.stat_answer?.refusal?.reason ?? result.no_answer_reason ?? null;
}

// Phase 1 RefusalReason (kosis_ai.schemas) → the title of the refusal card. The body is the backend's message.
export const REFUSALS: Record<string, { title: string; tone: Tone }> = {
  no_period: { title: "요청하신 기간의 자료가 이 통계표에 없습니다", tone: "amber" },
  no_item: { title: "요청하신 항목이 이 통계표에 없습니다", tone: "amber" },
  class_mismatch: { title: "요청하신 분류가 이 통계표와 맞지 않습니다", tone: "amber" },
  unit_mismatch: { title: "단위가 달라 함께 계산할 수 없습니다", tone: "amber" },
  not_aggregatable: { title: "이 지표는 기간이나 항목을 더해 합칠 수 없습니다", tone: "amber" },
  causal: { title: "이 질문은 원인 해석이나 예측을 다루지 않습니다", tone: "red" },
  missing_value: { title: "해당 칸에 값이 없습니다", tone: "gray" },
  out_of_scope: { title: "제공된 KOSIS 통계표가 다루는 범위 밖입니다", tone: "gray" },
};
export const UNKNOWN_REFUSAL = { title: "이 질문에는 답할 수 없습니다", tone: "gray" as Tone };

export function refusalTitle(reason: string | null): { title: string; tone: Tone } {
  return (reason && REFUSALS[reason]) || UNKNOWN_REFUSAL;
}

// Progress of one assistant message from the stream events it has so far (the generating screen).
// The backend sends phase 2 search nodes, then `stat` per table, then tokens, then `result`.
export const STEPS = ["질문 이해", "표 찾기", "계산", "답변"] as const;
const SEARCH_END = new Set(["gate", "guide", "refuse"]);

export function progress(message: Pick<ChatMessage, "parts">): number {
  let done = 0;
  for (const p of message.parts) {
    if (p.type === "data-node") done = Math.max(done, SEARCH_END.has(p.data.name) ? 2 : 1);
    else if (p.type === "data-stat") done = Math.max(done, 3);
    else if (p.type === "data-result") done = 4;
  }
  return done;
}

export const NODE_LABELS: Record<string, string> = {
  parse: "질의 조건 파싱",
  retrieve: "하이브리드 검색 (dense + BM25)",
  rerank1: "1차 리랭커",
  rerank2: "2차 리랭커 (LLM)",
  gate: "답 없음 판정",
  guide: "안내 답변 생성",
  refuse: "거절 답변",
};
