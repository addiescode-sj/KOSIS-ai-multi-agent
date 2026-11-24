"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { MemberChat, MemberChatList } from "@/lib/auth/schemas";
import { useAuth } from "@/lib/auth/use-me";
import type { ConversationItem } from "@/types/account";
import type { Conversation } from "@/types/conversation";

import { conversations } from "./conversations";
import { conversationFrom, itemsFromBrowser, itemsFromServer } from "./restore";
import { sessionHeaders } from "./session";
import { useConversation, useConversations } from "./use-conversations";

export const CHATS_KEY = ["chats"] as const;

async function json<T>(res: Response, parse: (v: unknown) => T): Promise<T> {
  if (!res.ok) throw new Error(res.status === 404 ? "not-found" : `대화 서버 오류 (${res.status})`);
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
    queryKey: CHATS_KEY,
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
      loading: auth.status === "loading", error: null, browser,
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

export type SavedConversation =
  | { status: "loading" }
  | { status: "missing"; member: boolean }
  | { status: "error"; message: string }
  | { status: "ready"; conversation: Conversation; member: boolean };

// One conversation for /chat/[id]: GET /chat/{id} for a member, this browser for a guest.
export function useSavedConversation(id: string): SavedConversation {
  const auth = useAuth();
  const member = auth.status === "member";
  const local = useConversation(id);
  const server = useQuery({
    queryKey: [...CHATS_KEY, id],
    enabled: member,
    staleTime: Infinity, // the open chat keeps its own messages from here on
    gcTime: 0, // opened again: read again, the copy misses the turns asked since
    queryFn: async () => json(await fetch(`/api/chats/${encodeURIComponent(id)}`, { cache: "no-store" }), (v) => MemberChat.parse(v)),
  });
  if (auth.status === "loading") return { status: "loading" };
  if (!member) {
    if (local === undefined) return { status: "loading" };
    return local ? { status: "ready", conversation: local, member: false } : { status: "missing", member: false };
  }
  if (server.isPending) return { status: "loading" };
  if (server.error) return server.error.message === "not-found" ? { status: "missing", member: true } : { status: "error", message: server.error.message };
  return { status: "ready", conversation: conversationFrom(server.data), member: true };
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
    onSuccess: () => client.invalidateQueries({ queryKey: CHATS_KEY }),
  });
}
