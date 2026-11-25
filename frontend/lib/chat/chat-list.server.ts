import type { QueryClient } from "@tanstack/react-query";

import { MemberChat, MemberChatList } from "@/lib/auth/schemas";
import { backendGet } from "@/lib/backend/server-read";

import { chatListPath, chatListQuery, chatPath, chatQuery } from "./queries";
import { chatFrom, chatPageFrom } from "./read";

// Server prefetches of the member conversation queries with the caller's login cookie. Not awaited by the caller:
// the pending query is dehydrated and streams in.

export function prefetchChatList(client: QueryClient, cookie: string): void {
  void client.prefetchInfiniteQuery({
    ...chatListQuery,
    queryFn: async () => chatPageFrom(await backendGet(chatListPath(null), cookie, MemberChatList)),
  });
}

export function prefetchChat(client: QueryClient, id: string, cookie: string): void {
  void client.prefetchQuery({
    ...chatQuery(id),
    queryFn: async () => chatFrom(await backendGet(chatPath(id), cookie, MemberChat)),
  });
}
