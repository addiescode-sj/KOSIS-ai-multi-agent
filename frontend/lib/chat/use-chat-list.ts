"use client";

import { useInfiniteQuery, useMutation, useQueryClient, useSuspenseQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { MemberChatList } from "@/lib/auth/schemas";
import { useAuth } from "@/lib/auth/use-me";
import type { ConversationItem } from "@/types/account";
import type { Conversation } from "@/types/conversation";
import { queryKeys } from "@/lib/query/keys";

import { conversations } from "./conversations";
import { chatFrom, type ChatRead } from "./read";
import { itemsFromBrowser, itemsFromServer } from "./restore";
import { sessionHeaders } from "./session";
import { useConversations } from "./use-conversations";

async function json<T>(res: Response, parse: (v: unknown) => T): Promise<T> {
  if (!res.ok) throw new Error(`대화 서버 오류 (${res.status})`);
  return parse(await res.json());
}

export type ChatList = {
  member: boolean;
  items: ConversationItem[];
  more: boolean; // the server has more than the pages read so far
  loadMore: () => void;
  loadingMore: boolean;
  moreFailed: boolean;
  loading: boolean;
  error: string | null; // nothing could be read
  browser: Conversation[]; // guest: the full saved conversations (the search page reads their text)
};

// The conversation list of the sidebar and /chat: GET /chat for a member, this browser for a guest.
export function useChatList(): ChatList {
  const auth = useAuth();
  const member = auth.status === "member";
  const browser = useConversations();
  const server = useInfiniteQuery({
    queryKey: queryKeys.chatList,
    enabled: member,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) => json(
      await fetch(`/api/chats${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ""}`, { cache: "no-store" }),
      (v) => MemberChatList.parse(v),
    ),
    getNextPageParam: (last) => last.next_cursor || null,
  });
  if (!member) {
    return {
      member: false, items: itemsFromBrowser(browser), more: false, loadMore: () => {}, loadingMore: false, moreFailed: false,
      loading: false, error: null, browser,
    };
  }
  return {
    member: true,
    items: itemsFromServer(server.data?.pages.flatMap((p) => p.items) ?? []),
    more: server.hasNextPage,
    loadMore: () => { if (!server.isFetching) void server.fetchNextPage(); },
    loadingMore: server.isFetchingNextPage,
    moreFailed: server.isFetchNextPageError,
    loading: server.isPending,
    error: server.error && !server.data ? server.error.message : null,
    browser: [],
  };
}

// One member conversation for /chat/[id] (GET /chat/{id}). The page prefetches it on the server with the login
// (SavedChatHydration), so this suspends only until that answer streams in.
export function useMemberChat(id: string): ChatRead {
  const client = useQueryClient();
  const { data } = useSuspenseQuery({
    queryKey: queryKeys.chat(id),
    queryFn: async () => {
      const res = await fetch(`/api/chats/${encodeURIComponent(id)}`, { cache: "no-store" }).catch(() => null);
      return chatFrom(res && { status: res.status, body: await res.json().catch(() => null) });
    },
    staleTime: Infinity, // the open chat keeps its own messages from here on
  });
  // Opened again later: read again (the copy misses the turns asked since). Dropped once no screen shows it; the
  // delay lets a remount (React's dev double effects) attach first.
  useEffect(() => () => {
    setTimeout(() => client.removeQueries({ queryKey: queryKeys.chat(id), exact: true, predicate: (q) => q.getObserversCount() === 0 }));
  }, [client, id]);
  return data;
}

// Delete one conversation: on the server (member: the conversation, guest: its thread) and from this browser.
export function useDeleteChat() {
  const client = useQueryClient();
  const auth = useAuth();
  return useMutation({
    mutationFn: async (c: { id: string; threadId: string | null }) => {
      if (auth.status === "member") {
        const res = await fetch(`/api/chats/${encodeURIComponent(c.id)}`, { method: "DELETE" });
        if (!res.ok && res.status !== 404) throw new Error(`삭제하지 못했습니다 (${res.status})`);
        return;
      }
      conversations.remove(c.id);
      // the thread's earlier turns on the server; best effort (a guest list lives in this browser either way)
      if (c.threadId) {
        await fetch(`/api/chats/${encodeURIComponent(c.threadId)}`, { method: "DELETE", headers: sessionHeaders() }).catch(() => null);
      }
    },
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.chatList }),
  });
}
