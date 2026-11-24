import type { MemberChat } from "../auth/schemas.ts";
import type { ConversationItem } from "../../types/account.ts";
import type { ChatMessage } from "../../types/chat.ts";
import type { Conversation } from "../../types/conversation.ts";

import { ChatResult } from "./schemas.ts";

export const UNRESTORABLE = "이 답은 저장된 형식이 달라 다시 보여 드릴 수 없습니다.";

// A member conversation from GET /chat/{id} as the chat screen's messages: each turn's question, then its saved
// ChatResult checked again with zod. A result that no longer parses is not redrawn from guesses: the turn shows
// a fixed notice instead. The thread id is the conversation id (contract 6장).
export function conversationFrom(chat: MemberChat): Conversation {
  const turns = [...chat.turns].sort((a, b) => a.turn - b.turn);
  const messages: ChatMessage[] = turns.flatMap((t): ChatMessage[] => {
    const question: ChatMessage = { id: `${chat.id}-q${t.turn}`, role: "user", parts: [{ type: "text", text: t.question }] };
    const parsed = ChatResult.safeParse(t.result);
    const answer: ChatMessage = parsed.success
      ? { id: `${chat.id}-a${t.turn}`, role: "assistant", parts: [
          { type: "data-result", data: parsed.data },
          { type: "text", text: parsed.data.answer },
        ] }
      : { id: `${chat.id}-a${t.turn}`, role: "assistant", parts: [{ type: "data-notice", data: { text: UNRESTORABLE } }] };
    return [question, answer];
  });
  const last = turns.at(-1);
  return { id: chat.id, title: chat.title, threadId: chat.id, updatedAt: last ? Date.parse(last.created_at) : 0, messages };
}

export function itemsFromServer(items: { id: string; title: string; updated_at: string }[]): ConversationItem[] {
  return items.map((c) => ({ id: c.id, title: c.title, updatedAt: Date.parse(c.updated_at), questions: null }));
}

export function itemsFromBrowser(list: Conversation[]): ConversationItem[] {
  return list.map((c) => ({
    id: c.id, title: c.title, updatedAt: c.updatedAt, questions: c.messages.filter((m) => m.role === "user").length,
  }));
}
