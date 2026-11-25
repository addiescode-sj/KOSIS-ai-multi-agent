import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";

import { MemberChat, MemberChatList } from "../auth/schemas.ts";
import { request } from "../backend/request.ts";
import { queryKeys } from "../query/keys.ts";

import { chatFrom, chatPageFrom, nextCursor, type ChatPage, type ChatRead } from "./read.ts";

// The member conversation queries, shared by the browser hooks (lib/chat/use-chat-list) and the server prefetch
// (lib/chat/chat-list.server): one key and one answer shape on both sides. Not a client module, so a server
// component can import it.

export const chatListPath = (cursor: string | null) => `/chat${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`;
export const chatPath = (id: string) => `/chat/${encodeURIComponent(id)}`;

// GET /chat?cursor= page by page (member), through /api/chats
export const chatListQuery = infiniteQueryOptions({
  queryKey: queryKeys.chatList,
  initialPageParam: null as string | null,
  queryFn: async ({ pageParam }): Promise<ChatPage> =>
    chatPageFrom(await request(`/api/chats${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ""}`, { responseSchema: MemberChatList })),
  getNextPageParam: (last, _all, lastParam) => nextCursor(last, lastParam),
});

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
