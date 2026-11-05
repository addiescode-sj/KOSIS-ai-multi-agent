import { createUIMessageStream, createUIMessageStreamResponse } from "ai";

import type { ChatMessage, SearchResult } from "@/lib/chat-types";
import { parseSSE } from "@/lib/sse";

// Proxy to the FastAPI agent graph (POST /chat/stream, T3-00), re-sent as the AI SDK UI message stream:
// each finished node -> data-node part, reply pieces -> data-draft part,
// the result -> data-result part (search results only) + the final answer as a text part (it replaces the draft).
// The answer and its citations come from the backend as they are; nothing here makes numbers or tables.
export async function POST(request: Request) {
  const { messages } = (await request.json()) as { messages: ChatMessage[] };
  const question = lastUserText(messages);
  if (!question) return Response.json({ error: "empty question" }, { status: 400 });
  // Read per request: a module-level constant would be fixed at build time in the standalone image
  const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

  const stream = createUIMessageStream<ChatMessage>({
    execute: async ({ writer }) => {
      writer.write({ type: "start" });
      const res = await fetch(`${backend}/chat/stream`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question }),
        signal: request.signal, // the user's stop reaches the backend
      });
      if (!res.ok || !res.body) throw new Error(`검색 서버 오류 (${res.status}): ${await detail(res)}`);

      let draft = "";
      for await (const { event, data } of parseSSE(res.body)) {
        const payload = JSON.parse(data);
        if (event === "token") {
          draft += payload.delta;
          writer.write({ type: "data-draft", id: "draft", data: { text: draft } }); // same id: the part is updated
          continue;
        }
        if (event === "error") throw new Error(`파이프라인 오류: ${payload.type} ${payload.detail}`);
        if (event === "result") {
          // The skeleton agent sends only { answer }; table cards appear once a result carries candidates (T3-01)
          if ("candidates" in payload) writer.write({ type: "data-result", id: "result", data: payload as SearchResult });
          writer.write({ type: "text-start", id: "answer" });
          writer.write({ type: "text-delta", id: "answer", delta: payload.answer });
          writer.write({ type: "text-end", id: "answer" });
        } else {
          writer.write({ type: "data-node", id: `node-${event}`, data: { name: event, seconds: payload.seconds } });
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

function lastUserText(messages: ChatMessage[]): string {
  const last = messages.findLast((m) => m.role === "user");
  return (last?.parts ?? []).map((p) => (p.type === "text" ? p.text : "")).join("").trim();
}

async function detail(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    return JSON.parse(text).detail ?? text;
  } catch {
    return text || res.statusText;
  }
}
