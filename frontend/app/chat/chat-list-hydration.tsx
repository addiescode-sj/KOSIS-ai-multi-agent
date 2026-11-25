import { dehydrate, HydrationBoundary } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { loginCookie } from "@/lib/backend/server-read";
import { prefetchChatList } from "@/lib/chat/chat-list.server";
import { makeQueryClient } from "@/lib/query/client";

// A screen that lists a member's conversations (/chat, /settings): with a login, GET /chat starts on the server
// beside /auth/me (root layout). Not awaited: the pending query goes down as a promise and the list streams in.
export async function ChatListHydration({ children }: { children: ReactNode }) {
  const cookie = await loginCookie();
  const client = makeQueryClient();
  if (cookie) prefetchChatList(client, cookie);
  return <HydrationBoundary state={dehydrate(client)}>{children}</HydrationBoundary>;
}
