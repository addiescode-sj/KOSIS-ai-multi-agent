import { z } from "zod";

// The login and member-conversation API of docs/service/api.md 6장 (not built in the backend yet).
// looseObject: a backend addition never breaks the page.

// GET /auth/me. The contract stores the Kakao member number and nickname only; email is shown only if one comes.
export const Me = z.looseObject({
  user_id: z.string(),
  nickname: z.string(),
  email: z.string().nullish(),
});
export type Me = z.infer<typeof Me>;

// GET /chat?cursor=
export const MemberChatList = z.looseObject({
  items: z.array(z.looseObject({ id: z.string(), title: z.string(), created_at: z.string(), updated_at: z.string() })),
  next_cursor: z.string().nullish(),
});
export type MemberChatList = z.infer<typeof MemberChatList>;

// GET /chat/{id}: the saved turns; `result` is the turn's ChatResult, checked again when it is drawn (lib/chat/restore)
export const MemberChat = z.looseObject({
  id: z.string(),
  title: z.string(),
  turns: z.array(z.looseObject({ turn: z.number().int(), question: z.string(), result: z.unknown(), created_at: z.string() })),
});
export type MemberChat = z.infer<typeof MemberChat>;
