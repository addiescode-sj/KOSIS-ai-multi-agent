import { z } from "zod";

import { TurnFeedback } from "../chat/schemas.ts";

// The login and member-conversation API of docs/service/api.md 6장 (openapi MeResponse, MeUpdate,
// ConversationList, ConversationDetail). looseObject: a backend addition never breaks the page.
// POST /auth/logout and DELETE /auth/me answer 204 with no body (lib/backend/request NoContent).

export const AVATAR_COLOR_CODES = ["blue", "green", "amber", "rose", "slate"] as const;
export const RetentionDays = z.union([z.literal(90), z.literal(30)]);

// GET /auth/me (and PATCH /auth/me's answer). The contract stores the Kakao member number and nickname only; email
// is shown only if one comes.
export const Me = z.looseObject({
  user_id: z.string(),
  nickname: z.string(),
  email: z.string().nullish(),
  display_name: z.string().nullable(),
  color: z.enum(AVATAR_COLOR_CODES),
  save_history: z.boolean(),
  retention_days: RetentionDays,
});
export type Me = z.infer<typeof Me>;

// PATCH /auth/me: only the fields sent change. display_name 1..20 characters without control characters, or null.
export const MeUpdate = z.strictObject({
  display_name: z.string().min(1).max(20).regex(/^[^\x00-\x1f\x7f]+$/).nullable().optional(),
  color: z.enum(AVATAR_COLOR_CODES).optional(),
  save_history: z.boolean().optional(),
  retention_days: RetentionDays.optional(),
});
export type MeUpdate = z.infer<typeof MeUpdate>;

export const ChatListItem = z.looseObject({
  id: z.string(), title: z.string(), created_at: z.string(), updated_at: z.string(), questions: z.number().int(),
});
export type ChatListItem = z.infer<typeof ChatListItem>;

// GET /chat?cursor=
export const MemberChatList = z.looseObject({
  items: z.array(ChatListItem),
  next_cursor: z.string().nullish(),
});
export type MemberChatList = z.infer<typeof MemberChatList>;

// GET /chat/{id}: the saved turns; `result` is the turn's ChatResult, checked again when it is drawn (lib/chat/restore:
// an old record that no longer parses becomes a notice, not a broken page), and the turn's last 👍/👎 (T3-55).
export const MemberChatTurn = z.looseObject({
  turn: z.number().int(),
  question: z.string(),
  result: z.unknown(),
  created_at: z.string(),
  feedback: TurnFeedback.nullish(),
});
export const MemberChat = z.looseObject({
  id: z.string(),
  title: z.string(),
  turns: z.array(MemberChatTurn),
});
export type MemberChat = z.infer<typeof MemberChat>;
