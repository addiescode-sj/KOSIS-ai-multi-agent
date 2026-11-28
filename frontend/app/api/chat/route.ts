import { createUIMessageStream, createUIMessageStreamResponse, type UIMessageStreamWriter } from "ai";
import { ZodError } from "zod";

import { callerHeaders } from "@/lib/backend/proxy";
import {
  droppedFailure, type Failure, failureFromEvent, failureFromStatus, finalMessage, retryKind, shouldReconnect,
} from "@/lib/chat/reconnect";
import { errorFrom, errorResponse, issuesText } from "@/lib/backend/errors";
import {
  ChatResult, ChatStreamRequest, ErrorEvent, Meta, NodeEvent, QUESTION_MAX, StatEvent, TokenEvent,
} from "@/lib/chat/schemas";
import { parseSSE } from "@/lib/sse";
import type { ChatMessage } from "@/types/chat";

import { ChatRequestBody, EXPIRED_NOTICE, lastQuestion } from "./schemas";

// Proxy to the FastAPI agent graph (POST /chat/stream), re-sent as the AI SDK UI message stream:
//   meta -> data-meta (thread_id for the next turn, request_id) + data-notice when the guest conversation expired,
//   each phase 2 search node -> data-node, stat -> data-stat, token -> data-draft,
//   result -> data-result (zod-checked) + the final answer as a text part.
// A retryable failure (dropped connection, 5xx, `error` with retryable true) reconnects once (data-reconnect);
// after that the turn ends with data-failure and a stream error (api.md 2 error, 10장 제안 8). 422 is never retried.
// The answer, its numbers and citations come from the backend as they are; nothing here makes them.
export async function POST(request: Request) {
  const parsed = ChatRequestBody.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return errorResponse(422, "invalid_request", issuesText(parsed.error));
  const { threadId, retryOf, inputMethod } = parsed.data;
  const question = lastQuestion(parsed.data.messages);
  if (!question) return errorResponse(422, "invalid_request", "질문을 입력해 주세요.");
  // Read per request: a module-level constant would be fixed at build time in the standalone image
  const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

  const headers = callerHeaders(request); // the member's login cookie or the guest session X-Session-Id (T3-54)
  headers.set("content-type", "application/json");

  const stream = createUIMessageStream<ChatMessage>({
    execute: async ({ writer }) => {
      writer.write({ type: "start" });
      let thread = threadId ?? null;
      let retry = retryOf ?? null;
      for (let reconnects = 0; ; reconnects++) {
        // the backend's request, checked before it is sent; a question it would refuse (over 200 characters)
        // ends the turn as the backend's 422 would: the error card, no reconnect
        const body = ChatStreamRequest.safeParse({
          question, thread_id: thread, retry_of: retry, retry_kind: retryKind(retry, reconnects), input_method: inputMethod,
        });
        const attempt = body.success
          ? await answerOnce(`${backend}/chat/stream`, headers, body.data, writer, request.signal, reconnects)
          : { failure: failureFromStatus(422, `질문은 ${QUESTION_MAX}자까지 입력할 수 있습니다.`), threadId: null };
        if (attempt.failure === null) return;
        const { failure } = attempt;
        if (!shouldReconnect(failure, reconnects)) {
          writer.write({
            type: "data-failure",
            id: "failure",
            data: { retryable: failure.retryable, status: failure.status, requestId: failure.requestId },
          });
          throw new Error(finalMessage(failure));
        }
        writer.write({ type: "data-reconnect", id: "reconnect", data: { attempt: reconnects + 1, connected: false } });
        thread = attempt.threadId ?? thread; // the thread the backend opened (a new one if the old had expired)
        retry = failure.requestId ?? retry; // the reconnect is linked to the attempt that failed
      }
    },
    onError: (error) => (error instanceof Error ? error.message : "알 수 없는 오류"),
  });
  return createUIMessageStreamResponse({
    stream,
    headers: { "cache-control": "no-cache, no-transform" }, // no compression buffering of the event stream
  });
}

type Attempt = { failure: Failure | null; threadId: string | null };

// One POST /chat/stream. Returns the failure to decide on instead of throwing; the user's stop (abort) and a reply
// that breaks the contract (bad JSON, a result zod rejects) are thrown: reconnecting would not help either.
async function answerOnce(
  url: string,
  headers: Headers,
  body: ChatStreamRequest,
  writer: UIMessageStreamWriter<ChatMessage>,
  signal: AbortSignal,
  reconnects: number,
): Promise<Attempt> {
  let res: Response;
  try {
    res = await fetch(url, { method: "POST", headers, body: JSON.stringify(body), signal });
  } catch (e) {
    if (signal.aborted) throw e;
    return { failure: droppedFailure(null), threadId: null };
  }
  if (!res.ok || !res.body) return { failure: failureFromStatus(res.status, await detail(res)), threadId: null };

  let draft = "";
  let threadId: string | null = null;
  let requestId: string | null = null;
  try {
    for await (const { event, data } of parseSSE(res.body)) {
      const payload = JSON.parse(data);
      if (event === "meta") {
        const meta = Meta.parse(payload);
        threadId = meta.thread_id;
        requestId = meta.trace_id ?? null;
        writer.write({ type: "data-meta", id: "meta", data: { threadId, requestId } });
        if (meta.expired) writer.write({ type: "data-notice", id: "expired", data: { text: EXPIRED_NOTICE } });
        if (reconnects > 0) writer.write({ type: "data-reconnect", id: "reconnect", data: { attempt: reconnects, connected: true } });
      } else if (event === "token") {
        draft += TokenEvent.parse(payload).delta;
        writer.write({ type: "data-draft", id: "draft", data: { text: draft } }); // same id: the part is updated
      } else if (event === "stat") {
        const stat = StatEvent.parse(payload);
        writer.write({ type: "data-stat", id: `stat-${stat.key}`, data: { key: stat.key, seconds: stat.seconds } });
      } else if (event === "error") {
        const failure = failureFromEvent(ErrorEvent.parse(payload));
        return { failure: { ...failure, requestId: failure.requestId ?? requestId }, threadId };
      } else if (event === "result") {
        const result = ChatResult.parse(payload);
        writer.write({ type: "data-result", id: "result", data: result });
        writer.write({ type: "text-start", id: "answer" });
        writer.write({ type: "text-delta", id: "answer", delta: result.answer });
        writer.write({ type: "text-end", id: "answer" });
        return { failure: null, threadId };
      } else {
        writer.write({ type: "data-node", id: `node-${event}`, data: { name: event, seconds: NodeEvent.parse(payload).seconds } });
      }
    }
  } catch (e) {
    if (signal.aborted || e instanceof ZodError || e instanceof SyntaxError) throw e;
    return { failure: droppedFailure(requestId), threadId }; // the body stopped mid-stream
  }
  return { failure: droppedFailure(requestId), threadId }; // the stream ended without `result` or `error`
}

// The message of a non-2xx answer: {"error": {code, message}}, FastAPI's 422 {"detail": [...]}, or the raw text
async function detail(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    return errorFrom(res.status, JSON.parse(text)).message;
  } catch {
    return text || res.statusText;
  }
}
