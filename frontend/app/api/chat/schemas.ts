import { z } from "zod";

// What only this proxy reads: what the browser posts besides the AI SDK messages. The SSE events are in lib/chat/schemas.

// A message part: a text part has its text; the others (data parts the screen drew) are not read here
const Part = z.union([
  z.looseObject({ type: z.literal("text"), text: z.string() }),
  z.looseObject({ type: z.string() }),
]);
const Message = z.looseObject({ role: z.string(), parts: z.array(Part) });

export const ChatRequestBody = z.object({
  messages: z.array(Message).min(1),
  threadId: z.string().min(1).nullish(),
  retryOf: z.string().min(1).nullish(), // "다시 시도": the failed turn's request_id (api.md 2 retry_of)
  inputMethod: z.enum(["direct", "clarify_option"]).default("direct"),
});

export const EXPIRED_NOTICE = "이전 대화가 만료되었습니다. 새 대화로 답합니다.";

/** The text of the last user message: the question sent to the backend. */
export function lastQuestion(messages: z.infer<typeof Message>[]): string {
  const last = messages.findLast((m) => m.role === "user");
  if (!last) return "";
  return last.parts.map((p) => ("text" in p && p.type === "text" && typeof p.text === "string" ? p.text : "")).join("").trim();
}
