// When the chat proxy reconnects to the backend on its own (api.md 2 `error`, 10장 제안 8: once). A failure the
// user can fix (422: bad input) is never retried; a dropped connection, a 5xx or an `error` event with
// `retryable: true` is retried once, then the turn ends with the "다시 시도" card.
// T3-67: every failure carries an error code; the card picks its sentence by code (lib/chat/errors.ts) and never
// shows the backend's exception name or text.

import { errorView } from "./errors.ts";

export const MAX_RECONNECTS = 1;

export type Failure = {
  retryable: boolean;
  status: number | null; // HTTP status of /chat/stream, null for an `error` event or a dropped connection
  code: string; // the error code (lib/chat/errors.ts ANSWER_ERRORS), or invalid_request for a 422
  message: string; // the server's own sentence: shown for a 422, or for a code the screen does not know
  requestId: string | null; // the failed attempt's request_id, sent as retry_of on the reconnect
};

// A backend error body as the proxy read it ({"error": {code, message, cause}}, api.md 0장)
export type ErrorReply = { code?: string | null; message?: string | null; cause?: string | null };

export function shouldReconnect(failure: Failure, reconnects: number): boolean {
  return failure.retryable && failure.status !== 422 && reconnects < MAX_RECONNECTS;
}

/** A non-2xx answer of POST /chat/stream. 503 (agent not loaded yet: `cause` says why) and other 5xx may pass on
 *  the next try. */
export function failureFromStatus(status: number, reply: ErrorReply): Failure {
  const message = reply.message ?? "";
  if (status === 422) {
    return { retryable: false, status, code: "invalid_request", message: message || "질문을 입력해 주세요.", requestId: null };
  }
  const code = reply.cause ?? (status >= 500 ? "backend_unreachable" : reply.code ?? "internal_error");
  return { retryable: status >= 500, status, code, message, requestId: null };
}

/** The backend's `error` event: `{code, message, retryable, request_id, type, detail}`. `type`·`detail` are for logs;
 *  an older backend sends no code (internal_error) and no `retryable` (not retried). */
export function failureFromEvent(payload: { code?: unknown; message?: unknown; retryable?: unknown; request_id?: unknown }): Failure {
  return {
    retryable: payload.retryable === true,
    status: null,
    code: typeof payload.code === "string" && payload.code ? payload.code : "internal_error",
    message: typeof payload.message === "string" ? payload.message : "",
    requestId: typeof payload.request_id === "string" ? payload.request_id : null,
  };
}

/** The backend could not be reached at all (the request to it threw before any answer): restarting, or down. */
export function unreachableFailure(): Failure {
  return { retryable: true, status: null, code: "backend_unreachable", message: "", requestId: null };
}

/** The connection broke mid-answer (the body stopped) or the stream ended without `result`. */
export function droppedFailure(requestId: string | null): Failure {
  return { retryable: true, status: null, code: "connection_lost", message: "", requestId };
}

/** What the error card says once the turn has given up: the input explanation for a 422, else the code's text. */
export function finalMessage(failure: Failure): string {
  return failure.status === 422 ? failure.message : errorView(failure.code, failure.message).text;
}
