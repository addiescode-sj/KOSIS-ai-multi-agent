import { z } from "zod";

import { createPersisted, persisted, type KV, type PersistedStore } from "../storage/persisted.ts";
import type { ChatMessage } from "../../types/chat.ts";
import type { Conversation } from "../../types/conversation.ts";

import { TurnFeedback } from "./schemas.ts";
import { textOf } from "./text.ts";

// A guest's conversations live in this tab only (sessionStorage, api.md 1장), as they were shown (results, charts,
// sources): the server keeps no guest list. threadId is the backend's id for the follow-up turns. A list kept in
// localStorage before T3-55 moves into the first tab opened after it, once.

export const STORAGE_KEY = "kosis.conversations.v1";

const StoredConversation = z.looseObject({
  id: z.string(),
  title: z.string(),
  threadId: z.string().nullable(),
  updatedAt: z.number(),
  messages: z.array(z.looseObject({ id: z.string(), role: z.string(), parts: z.array(z.looseObject({ type: z.string() })) })),
  feedback: z.record(z.string(), TurnFeedback).optional(),
});

// Entry by entry: one broken conversation is dropped, the others stay
const StoredList = z.array(z.unknown()).transform((items) =>
  items.flatMap((item) => (StoredConversation.safeParse(item).success ? [item as Conversation] : [])),
);

const NONE: Conversation[] = [];

export type ConversationStore = ReturnType<typeof storeOn>;

/** A store over the given storage (tests, a page reload = a new store over the same storage). */
export function createStore(storage: () => KV | null, legacy?: () => KV | null): ConversationStore {
  return storeOn(createPersisted<Conversation[]>(STORAGE_KEY, StoredList, { storage, fallback: NONE, legacy }));
}

function storeOn(store: PersistedStore<Conversation[]>) {
  const read = store.get;

  return {
    // newest first; the same array until the storage changes (useSyncExternalStore needs a stable snapshot)
    list: read,
    get: (id: string) => read().find((c) => c.id === id) ?? null,
    save(conversation: Conversation) {
      // a save from the chat screen does not carry the feedback map: keep what this tab already has
      const kept = read().find((c) => c.id === conversation.id)?.feedback;
      const next = conversation.feedback === undefined && kept ? { ...conversation, feedback: kept } : conversation;
      store.set([next, ...read().filter((c) => c.id !== conversation.id)].sort((a, b) => b.updatedAt - a.updatedAt));
    },
    remove(id: string) {
      store.set(read().filter((c) => c.id !== id));
    },
    // The 👍/👎 given on one answer, or null to clear it. Found by thread id (or conversation id), else by the
    // answer's request id. false when this tab has no such conversation.
    setTurnFeedback(threadId: string | null, requestId: string, feedback: TurnFeedback | null): boolean {
      const list = read();
      const target =
        list.find((c) => threadId !== null && (c.threadId === threadId || c.id === threadId)) ??
        list.find((c) => c.messages.some((m) => requestIdsOf(m).includes(requestId)));
      if (!target) return false;
      const map = { ...(target.feedback ?? {}) };
      if (feedback) map[requestId] = { value: feedback.value, category: feedback.category };
      else delete map[requestId];
      store.set(list.map((c) => (c === target ? { ...c, feedback: map } : c)));
      return true;
    },
    subscribe: store.subscribe,
  };
}

function requestIdsOf(m: ChatMessage): string[] {
  return m.parts.flatMap((p) => (p.type === "data-result" && p.data.request_id ? [p.data.request_id] : []));
}

/** The 👍/👎 kept for one answer of a conversation, or null. */
export function feedbackOf(conversation: Pick<Conversation, "feedback"> | null | undefined, requestId: string | null | undefined): TurnFeedback | null {
  if (!conversation?.feedback || !requestId) return null;
  return conversation.feedback[requestId] ?? null;
}

export function titleOf(messages: ChatMessage[]): string {
  const first = messages.find((m) => m.role === "user");
  const text = first ? textOf(first).trim() : "";
  return text.length > 40 ? `${text.slice(0, 40)}…` : text || "새 대화";
}

// Case-insensitive match on the title and on every question and answer text of the conversation.
export function matchConversations(list: Conversation[], query: string): Conversation[] {
  const q = query.trim().toLowerCase();
  if (!q) return list;
  return list.filter((c) =>
    [c.title, ...c.messages.map(answerOrText)].some((t) => t.toLowerCase().includes(q)),
  );
}

function answerOrText(m: ChatMessage): string {
  const result = m.parts.find((p) => p.type === "data-result");
  return result && result.type === "data-result" ? result.data.answer : textOf(m);
}

// The tab's one store (shared with usePersisted(STORAGE_KEY, ...) readers through the registry)
export const conversations = storeOn(
  persisted<Conversation[]>(STORAGE_KEY, StoredList, { area: "session", fallback: NONE, legacyArea: "local" }),
);

/** Module form of the contract: keep the 👍/👎 of a guest's answer with its turn (this tab). */
export function setTurnFeedback(threadId: string | null, requestId: string, feedback: TurnFeedback | null): boolean {
  return conversations.setTurnFeedback(threadId, requestId, feedback);
}
