import { z } from "zod";

// What only this proxy reads: the first SSE event of a turn, and what the browser posts besides the AI SDK messages.

export const Meta = z.looseObject({ thread_id: z.string(), trace_id: z.string().nullish() });

export const ChatRequestBody = z.object({
  messages: z.array(z.looseObject({ role: z.string(), parts: z.array(z.looseObject({ type: z.string() })) })),
  threadId: z.string().min(1).nullish(),
});
