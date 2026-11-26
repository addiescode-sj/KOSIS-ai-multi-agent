import type { Citation } from "../../types/answer.ts";
import type { EventFields } from "../events/events.ts";

// The three screen events of one answer (api.md 5장, log-schema 4): which fields each carries. Pure, so the chat
// components only call track() with what these return. Feedback is not a screen event here: POST /feedback only.

export type TurnIds = { requestId: string | null; threadId: string | null; turn: number | null };

function common(ids: TurnIds) {
  return { request_id: ids.requestId, thread_id: ids.threadId, turn: ids.turn };
}

/** 출처 link clicked. null when the table has no rank in this answer (the event needs one). */
export function citationClick(c: Citation, ids: TurnIds): EventFields<"citation_click"> | null {
  if (c.rank === null) return null;
  return { ...common(ids), table_keys: [c.key], props: { table_key: c.key, rank: c.rank } };
}

/** 복사 button: common fields only (no answer text is sent). */
export function copyAnswer(ids: TurnIds, tableKey: string | null): EventFields<"copy_answer"> {
  return { ...common(ids), table_keys: tableKey ? [tableKey] : [] };
}

/** The user stopped or left while the answer streamed. null when no stream was running. */
export function streamAbort(ids: TurnIds, startedAt: number | null, now: number): EventFields<"stream_abort"> | null {
  if (startedAt === null) return null;
  return { ...common(ids), props: { elapsed_ms: Math.max(0, Math.round(now - startedAt)) } };
}
