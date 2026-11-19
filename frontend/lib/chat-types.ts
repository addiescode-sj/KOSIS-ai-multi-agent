import type { UIMessage } from "ai";

import type { ChatResult } from "./schemas.ts";

// Data parts /api/chat writes into the assistant message (AI SDK UI message stream):
//   node   — a finished phase 2 search node (parse, retrieve, rerank1, rerank2, gate, ...) and its seconds
//   stat   — phase 1 ran on one table (summarize_stat)
//   draft  — the answer sentence so far (tokens the backend sends after phase 1 checked the numbers)
//   result — the turn's ChatResult, parsed with zod; the final answer replaces the draft
//   meta   — the backend thread_id, sent back on the next turn (multi-turn)
//   notice — a note from the proxy, e.g. the backend no longer has the earlier turns
export type ChatData = {
  node: { name: string; seconds: number };
  stat: { key: string; seconds: number };
  draft: { text: string };
  result: ChatResult;
  meta: { threadId: string };
  notice: { text: string };
};

export type ChatMessage = UIMessage<never, ChatData>;

export function textOf(message: Pick<ChatMessage, "parts">): string {
  return message.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
}
