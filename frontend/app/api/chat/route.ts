import { createUIMessageStream, createUIMessageStreamResponse } from "ai";

import { callerHeaders } from "@/lib/backend/proxy";
import { ChatResult } from "@/lib/chat/schemas";
import { textOf } from "@/lib/chat/text";
import { parseSSE } from "@/lib/sse";
import type { ChatMessage } from "@/types/chat";

import { ChatRequestBody, Meta } from "./schemas";

// Proxy to the FastAPI agent graph (POST /chat/stream), re-sent as the AI SDK UI message stream:
//   meta -> data-meta (thread_id for the next turn), each phase 2 search node -> data-node, stat -> data-stat,
//   token -> data-draft, result -> data-result (zod-checked) + the final answer as a text part.
// The answer, its numbers and citations come from the backend as they are; nothing here makes them.
export async function POST(request: Request) {
  const parsed = ChatRequestBody.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return Response.json({ error: "bad request" }, { status: 400 });
  const { threadId } = parsed.data;
  const messages = parsed.data.messages as unknown as ChatMessage[];
  const last = messages.findLast((m) => m.role === "user");
  const question = last ? textOf(last).trim() : "";
  if (!question) return Response.json({ error: "empty question" }, { status: 400 });
  // Read per request: a module-level constant would be fixed at build time in the standalone image
  const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

  const headers = callerHeaders(request); // the member's login cookie or the guest session (T3-54)
  headers.set("content-type", "application/json");

  const stream = createUIMessageStream<ChatMessage>({
    execute: async ({ writer }) => {
      writer.write({ type: "start" });
      // The checkpointer is in memory: after a backend restart the thread is gone and this turn starts fresh
      if (threadId && (await threadLost(backend, threadId, request.signal))) {
        writer.write({
          type: "data-notice",
          id: "notice",
          data: { text: "서버가 다시 시작되어 이전 대화 맥락 없이 새로 답합니다." },
        });
      }
      const res = await fetch(`${backend}/chat/stream`, {
        method: "POST",
        headers,
        body: JSON.stringify({ question, thread_id: threadId ?? null }),
        signal: request.signal, // the user's stop reaches the backend
      });
      if (!res.ok || !res.body) throw new Error(`답변 서버 오류 (${res.status}): ${await detail(res)}`);

      let draft = "";
      for await (const { event, data } of parseSSE(res.body)) {
        const payload = JSON.parse(data);
        if (event === "meta") {
          writer.write({ type: "data-meta", id: "meta", data: { threadId: Meta.parse(payload).thread_id } });
        } else if (event === "token") {
          draft += payload.delta;
          writer.write({ type: "data-draft", id: "draft", data: { text: draft } }); // same id: the part is updated
        } else if (event === "stat") {
          writer.write({ type: "data-stat", id: `stat-${payload.key}`, data: { key: payload.key, seconds: payload.seconds } });
        } else if (event === "error") {
          throw new Error(`처리 중 오류: ${payload.type} ${payload.detail}`);
        } else if (event === "result") {
          const result = ChatResult.parse(payload);
          writer.write({ type: "data-result", id: "result", data: result });
          writer.write({ type: "text-start", id: "answer" });
          writer.write({ type: "text-delta", id: "answer", delta: result.answer });
          writer.write({ type: "text-end", id: "answer" });
        } else {
          writer.write({ type: "data-node", id: `node-${event}`, data: { name: event, seconds: payload.seconds ?? 0 } });
        }
      }
    },
    onError: (error) => (error instanceof Error ? error.message : "알 수 없는 오류"),
  });
  return createUIMessageStreamResponse({
    stream,
    headers: { "cache-control": "no-cache, no-transform" }, // no compression buffering of the event stream
  });
}

async function threadLost(backend: string, threadId: string, signal: AbortSignal): Promise<boolean> {
  const res = await fetch(`${backend}/threads/${encodeURIComponent(threadId)}`, { signal }).catch(() => null);
  return res?.status === 404;
}

async function detail(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    return JSON.parse(text).detail ?? text;
  } catch {
    return text || res.statusText;
  }
}
