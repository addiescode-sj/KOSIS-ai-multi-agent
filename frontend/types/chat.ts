import type { UIMessage } from "ai";

import type { ChatResult } from "../lib/chat/schemas.ts";

// Data parts /api/chat writes into the assistant message (AI SDK UI message stream):
//   node   — a finished phase 2 search node (parse, retrieve, rerank1, rerank2, gate, ...) and its seconds
//   stat   — phase 1 ran on one table (summarize_stat)
//   draft  — the answer sentence so far (tokens the backend sends after phase 1 checked the numbers)
//   result — the turn's ChatResult, parsed with zod; the final answer replaces the draft
//   meta      — the backend thread_id, sent back on the next turn (multi-turn), and this attempt's request_id
//   notice    — a note from the proxy, e.g. the guest conversation expired (meta.expired)
//   reconnect — the proxy is reconnecting once after a retryable failure (api.md 2 error); connected once its meta came
//   failure   — why the turn ended without a result (written just before the stream error)
export type ChatData = {
  node: { name: string; seconds: number };
  stat: { key: string; seconds: number };
  draft: { text: string };
  result: ChatResult;
  meta: { threadId: string; requestId?: string | null };
  notice: { text: string };
  reconnect: { attempt: number; connected: boolean };
  failure: { retryable: boolean; status: number | null; requestId: string | null };
};

export type ChatMessage = UIMessage<never, ChatData>;

// How a question was sent (api.md 2 input_method): typed, or a 되묻기 option button
export type InputMethod = "direct" | "clarify_option";

export type Ask = (text: string, method?: InputMethod) => void;
