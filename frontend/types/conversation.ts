import type { TurnFeedback } from "../lib/chat/schemas.ts";

import type { ChatMessage } from "./chat.ts";

// One conversation as this browser keeps it (lib/chat/conversations). threadId is the backend's id for follow-ups.
export type Conversation = {
  id: string;
  title: string;
  threadId: string | null;
  updatedAt: number; // epoch ms
  messages: ChatMessage[];
  // the 👍/👎 given on its answers, by the answer's request_id (T3-55); a member's comes from GET /chat/{id}
  feedback?: Record<string, TurnFeedback>;
};
