import type { ChatMessage } from "./chat.ts";

// One conversation as this browser keeps it (lib/chat/conversations). threadId is the backend's id for follow-ups.
export type Conversation = {
  id: string;
  title: string;
  threadId: string | null;
  updatedAt: number; // epoch ms
  messages: ChatMessage[];
};
