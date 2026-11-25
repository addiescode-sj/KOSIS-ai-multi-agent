import { infiniteQueryOptions, queryOptions, type QueryClient } from "@tanstack/react-query";

import { MemberChat, MemberChatList } from "../auth/schemas.ts";
import { request } from "../backend/request.ts";
import { queryKeys } from "../query/keys.ts";

import { chatFrom, chatPageFrom, nextCursor, type ChatPage, type ChatRead } from "./read.ts";

// The member conversation queries, shared by the browser hooks (lib/chat/use-chat-list) and the server prefetch
// (lib/chat/chat-list.server): one key and one answer shape on both sides. Not a client module, so a server
// component can import it.

export const chatListPath = (cursor: string | null) => `/chat${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`;
export const chatPath = (id: string) => `/chat/${encodeURIComponent(id)}`;

// GET /chat?cursor= page by page (member), through /api/chats. Read once (the server prefetch or the first read in
// the browser) and kept: no refetch on mount, focus or reconnect, and not dropped while unused. Only a change to the
// list replaces it, through chatListChanged (trigger-based caching); logout clears it with the rest of queryKeys.chats.
export const chatListQuery = infiniteQueryOptions({
  queryKey: queryKeys.chatList,
  initialPageParam: null as string | null,
  queryFn: async ({ pageParam }): Promise<ChatPage> =>
    chatPageFrom(await request(`/api/chats${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ""}`, { responseSchema: MemberChatList })),
  getNextPageParam: (last, _all, lastParam) => nextCursor(last, lastParam),
  staleTime: Infinity,
  gcTime: Infinity,
});

// The only things that change a member's conversation list; each calls chatListChanged.
export const ChatListChange = {
  TURN_SAVED: "turn_saved", // a reply ended and the backend saved the turn (new conversation, newer updated_at)
  CHAT_DELETED: "chat_deleted", // DELETE /chat/{id}
} as const;
export type ChatListChange = (typeof ChatListChange)[keyof typeof ChatListChange];

// Invalidate the cached list at once: a mounted list refetches now, one not on screen the next time a screen reads it.
// Returns the change it handled, so a caller (or a test) can see which trigger ran.
export async function chatListChanged(client: QueryClient, change: ChatListChange): Promise<ChatListChange> {
  await client.invalidateQueries({ queryKey: queryKeys.chatList });
  return change;
}

// GET /chat/{id} (member), through /api/chats/{id}. staleTime: the open chat keeps its own messages from here on.
// Dropped from the cache a second after no screen shows it (gcTime, lib/query/client), so opening it again reads
// the turns asked since.
export function chatQuery(id: string) {
  return queryOptions({
    queryKey: queryKeys.chat(id),
    queryFn: async (): Promise<ChatRead> =>
      chatFrom(await request(`/api/chats/${encodeURIComponent(id)}`, { responseSchema: MemberChat })),
    staleTime: Infinity,
  });
}
