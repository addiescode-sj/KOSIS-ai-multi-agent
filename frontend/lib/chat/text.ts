import type { ChatMessage } from "../../types/chat.ts";

export function textOf(message: Pick<ChatMessage, "parts">): string {
  return message.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
}
