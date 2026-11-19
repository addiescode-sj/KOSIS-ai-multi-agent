import type { ChatMessage } from "./chat-types.ts";
import { textOf } from "./chat-types.ts";

// Conversations live in this browser only (guest use): GET /threads/{id} returns text without results, and the
// backend checkpointer is in memory, so the turns as they were shown (results, charts, sources) are kept here.
// threadId is the backend's id for the follow-up turns; it is gone after a backend restart (the proxy says so).

export type Conversation = {
  id: string;
  title: string;
  threadId: string | null;
  updatedAt: number; // epoch ms
  messages: ChatMessage[];
};

type KV = Pick<Storage, "getItem" | "setItem">;

export const STORAGE_KEY = "kosis.conversations.v1";

export function createStore(storage: () => KV | null) {
  const listeners = new Set<() => void>();
  let cacheRaw: string | null | undefined;
  let cache: Conversation[] = [];

  function read(): Conversation[] {
    const raw = storage()?.getItem(STORAGE_KEY) ?? null;
    if (raw !== cacheRaw) {
      cacheRaw = raw;
      cache = parse(raw);
    }
    return cache;
  }

  function write(list: Conversation[]) {
    storage()?.setItem(STORAGE_KEY, JSON.stringify(list));
    listeners.forEach((l) => l());
  }

  return {
    // newest first; the same array until the storage changes (useSyncExternalStore needs a stable snapshot)
    list: read,
    get: (id: string) => read().find((c) => c.id === id) ?? null,
    save(conversation: Conversation) {
      write([conversation, ...read().filter((c) => c.id !== conversation.id)].sort((a, b) => b.updatedAt - a.updatedAt));
    },
    remove(id: string) {
      write(read().filter((c) => c.id !== id));
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

function parse(raw: string | null): Conversation[] {
  if (!raw) return [];
  try {
    const value = JSON.parse(raw);
    return Array.isArray(value) ? value : [];
  } catch {
    return []; // a broken entry must not take the page down
  }
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

export const conversations = createStore(() => (typeof window === "undefined" ? null : window.localStorage));
