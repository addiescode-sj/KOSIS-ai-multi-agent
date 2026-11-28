// When the chat proxy reconnects to the backend on its own (api.md 2 `error`, 10장 제안 8: once). A failure the
// user can fix (422: bad input) is never retried; a dropped connection, a 5xx or an `error` event with
// `retryable: true` is retried once, then the turn ends with the "다시 시도" card.

export const MAX_RECONNECTS = 1;

export type Failure = {
  retryable: boolean;
  status: number | null; // HTTP status of /chat/stream, null for an `error` event or a dropped connection
  message: string; // shown on the error card when no reconnect is left
  requestId: string | null; // the failed attempt's request_id, sent as retry_of on the reconnect
};

export const EXHAUSTED = "잠시 뒤 다시 시도해 주세요.";

/** Who sent retry_of (T3-68, logged as request_logs.retry_kind): the 다시 시도 button on the first attempt, this proxy
 *  on a reconnect. null when nothing is retried. Keeps the user's retry rate free of automatic reconnects. */
export function retryKind(retryOf: string | null, reconnects: number): "user" | "auto" | null {
  if (retryOf === null) return null;
  return reconnects > 0 ? "auto" : "user";
}

export function shouldReconnect(failure: Failure, reconnects: number): boolean {
  return failure.retryable && failure.status !== 422 && reconnects < MAX_RECONNECTS;
}

/** A non-2xx answer of POST /chat/stream. 503 (agent not loaded yet) and other 5xx may pass on the next try. */
export function failureFromStatus(status: number, detail: string): Failure {
  if (status === 422) return { retryable: false, status, message: detail || "질문을 입력해 주세요.", requestId: null };
  return { retryable: status >= 500, status, message: `답변 서버 오류 (${status}): ${detail}`, requestId: null };
}

/** The backend's `error` event: `{type, detail, retryable, request_id}`. Older backends send no `retryable`. */
export function failureFromEvent(payload: { type?: unknown; detail?: unknown; retryable?: unknown; request_id?: unknown }): Failure {
  return {
    retryable: payload.retryable === true,
    status: null,
    message: `처리 중 오류: ${String(payload.type ?? "")} ${String(payload.detail ?? "")}`.trim(),
    requestId: typeof payload.request_id === "string" ? payload.request_id : null,
  };
}

/** The connection broke (fetch threw, the body stopped) or the stream ended without `result`. */
export function droppedFailure(requestId: string | null): Failure {
  return { retryable: true, status: null, message: "답변 서버와 연결이 끊겼습니다.", requestId };
}

/** What the error card says once the turn has given up. */
export function finalMessage(failure: Failure): string {
  return failure.retryable ? EXHAUSTED : failure.message;
}
