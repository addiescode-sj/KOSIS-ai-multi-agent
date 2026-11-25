import { MemberChat } from "../auth/schemas.ts";

// GET /chat/{id}'s answer as a value, never a thrown error: the query is read with useSuspenseQuery (on the server
// too), and "not found" is a screen of its own, not an error boundary.
export type ChatRead =
  | { status: "ready"; chat: MemberChat }
  | { status: "missing" }
  | { status: "error"; message: string };

export function chatFrom(res: { status: number; body: unknown } | null): ChatRead {
  if (res === null) return { status: "error", message: "대화 서버에 연결하지 못했습니다" };
  if (res.status === 404) return { status: "missing" };
  if (res.status < 200 || res.status >= 300) return { status: "error", message: `대화 서버 오류 (${res.status})` };
  const chat = MemberChat.safeParse(res.body);
  return chat.success ? { status: "ready", chat: chat.data } : { status: "error", message: "대화 형식이 다릅니다" };
}
