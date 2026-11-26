import type { ApiResult } from "../backend/request.ts";
import type { MemberChat, MemberChatList } from "../auth/schemas.ts";

// GET /chat/{id}'s answer as a value, never a thrown error: the query is read with useSuspenseQuery (on the server
// too), and "not found" is a screen of its own, not an error boundary. The body was checked with MemberChat.
export type ChatRead =
  | { status: "ready"; chat: MemberChat }
  | { status: "missing" }
  | { status: "error"; message: string };

export function chatFrom(res: ApiResult<MemberChat>): ChatRead {
  if (res.ok) return { status: "ready", chat: res.data };
  if (res.status === 404) return { status: "missing" };
  if (res.status === null) return { status: "error", message: "대화 서버에 연결하지 못했습니다" };
  if (res.code === "bad_response") return { status: "error", message: "대화 형식이 다릅니다" };
  return { status: "error", message: `대화 서버 오류 (${res.status})` };
}

// One page of GET /chat?cursor= as a value: a failed page stays in the list of pages, so "load more" can ask for the
// same cursor again and the pages read before stay on screen.
export type ChatPage =
  | { ok: true; items: MemberChatList["items"]; next_cursor: string | null }
  | { ok: false; message: string };

export function chatPageFrom(res: ApiResult<MemberChatList>): ChatPage {
  if (res.ok) return { ok: true, items: res.data.items, next_cursor: res.data.next_cursor || null };
  if (res.status === null) return { ok: false, message: "대화 서버에 연결하지 못했습니다" };
  if (res.code === "bad_response") return { ok: false, message: "대화 목록 형식이 다릅니다" };
  return { ok: false, message: `대화 서버 오류 (${res.status})` };
}

/** The cursor of the page after `last`: the same cursor again after a failed page, null when there is no more. */
export function nextCursor(last: ChatPage, lastCursor: string | null): string | null {
  if (!last.ok) return lastCursor; // null after a failed first page: that one is read again by refetch
  return last.next_cursor;
}
