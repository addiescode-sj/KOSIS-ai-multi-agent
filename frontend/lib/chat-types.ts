import type { UIMessage } from "ai";

// Mirrors of the backend Pydantic models (src/kosis_rag/schemas.py); the UI shows them as they come
export type TableCandidate = {
  key: string;
  org_id: string;
  tbl_id: string;
  table_name: string;
  survey_name: string;
  organization: string;
  period: string;
  url: string;
  score: number | null;
};

export type SearchResult = {
  question: string;
  candidates: TableCandidate[]; // the tables shown to the guide LLM
  picked: TableCandidate[]; // the ones the answer recommends and cites (T2-39); [] on refusal
  no_answer: boolean;
  no_answer_reason: string | null;
  answer: string;
  answer_origin: "llm" | "template" | "refusal";
  llm_status: string | null;
};

// One finished graph node from /search/stream: its name and its own trace entry (seconds, ...)
export type NodeStep = { name: string; seconds: number };

// draft = the guide reply so far, only the part that already passed the backend check (T2-38); the final answer replaces it
export type ChatMessage = UIMessage<never, { node: NodeStep; draft: { text: string }; result: SearchResult }>;

export const NODE_LABELS: Record<string, string> = {
  parse: "질의 조건 파싱",
  retrieve: "하이브리드 검색 (dense + BM25)",
  rerank1: "1차 리랭커",
  rerank2: "2차 리랭커 (LLM)",
  gate: "답 없음 판정",
  guide: "안내 답변 생성",
  refuse: "거절 답변",
};
