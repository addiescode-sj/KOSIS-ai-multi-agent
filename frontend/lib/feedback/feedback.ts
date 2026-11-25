import { request, type ApiResult } from "../backend/request.ts";
import { sessionHeaders } from "../chat/session.ts";

import {
  DOWN_CATEGORIES, FEEDBACK_CATEGORIES, FeedbackRequest, FeedbackResponse, REASON_MAX, UP_CATEGORIES,
  type FeedbackCategory, type FeedbackValue,
} from "./schemas.ts";

// 👍/👎 on one answer: POST /feedback through /api/feedback (api.md 3장). The one place feedback is stored; the
// screen shows what was sent from the mutation, and a member's restored chat from GET /chat/{id}.

export { DOWN_CATEGORIES, FEEDBACK_CATEGORIES, REASON_MAX, UP_CATEGORIES, type FeedbackCategory, type FeedbackValue };

export const CATEGORY_LABELS: Record<FeedbackCategory, string> = {
  accurate_number: "수치가 정확함",
  right_table: "원하는 표를 찾음",
  easy: "설명이 이해하기 쉬움",
  chart_helpful: "차트가 도움이 됨",
  wrong_number: "수치가 틀림",
  wrong_table: "표가 다름",
  hard: "이해하기 어려움",
  other: "기타",
};

export type FeedbackInput = {
  requestId: string;
  value: FeedbackValue;
  category: string | null; // null: the value only, no choice made (api.md 3장 제안값)
  reason?: string;
};

export type FeedbackResult = { ok: true; updated: boolean } | { ok: false; code: string; message: string };

/** What the form may send, in the words the form shows: null when fine. FeedbackRequest checks the same again. */
export function feedbackProblem(input: FeedbackInput): string | null {
  if (input.category !== null && !(FEEDBACK_CATEGORIES[input.value] as readonly string[]).includes(input.category)) {
    return "선택지를 다시 골라 주세요.";
  }
  const reason = input.reason?.trim() ?? "";
  if ([...reason].length > REASON_MAX) return `이유는 ${REASON_MAX}자까지 적을 수 있습니다.`;
  if (input.category === "other" && !reason) return "기타를 고르면 이유를 입력해야 합니다.";
  return null;
}

/** The POST /feedback body, before its schema check: reason trimmed, left out when empty. */
export function feedbackBody(input: FeedbackInput) {
  const reason = input.reason?.trim();
  return {
    request_id: input.requestId,
    value: input.value,
    category: input.category,
    ...(reason ? { reason } : {}),
  };
}

const MESSAGES: Record<number, string> = {
  404: "이 답에는 더 이상 피드백을 남길 수 없습니다.",
  422: "피드백 내용을 확인해 주세요.",
  429: "피드백을 너무 자주 보냈습니다. 잠시 뒤 다시 시도해 주세요.",
};

/** The answer as the form reads it; a fixed message when the server sent none of its own. */
export function feedbackFrom(res: ApiResult<FeedbackResponse>): FeedbackResult {
  if (res.ok) return { ok: true, updated: res.data.updated };
  if (res.code === "network") return { ok: false, code: "network", message: "피드백을 보내지 못했습니다. 연결을 확인해 주세요." };
  const fixed = res.status !== null ? MESSAGES[res.status] : undefined;
  const own = !res.code.startsWith("http_") && res.code !== "bad_response";
  return { ok: false, code: res.code, message: own ? res.message : (fixed ?? res.message) };
}

export async function sendFeedback(input: FeedbackInput): Promise<FeedbackResult> {
  const problem = feedbackProblem(input);
  if (problem) return { ok: false, code: "invalid_feedback", message: problem };
  return feedbackFrom(await request("/api/feedback", {
    method: "POST",
    headers: sessionHeaders(),
    body: feedbackBody(input),
    bodySchema: FeedbackRequest,
    responseSchema: FeedbackResponse,
  }));
}
