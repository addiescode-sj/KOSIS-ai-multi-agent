"use client";

import {
  useInfiniteQuery, useMutation, useQueryClient, useSuspenseInfiniteQuery, useSuspenseQuery, usePrefetchQuery,
  type InfiniteData,
} from "@tanstack/react-query";

import { useAuth } from "@/lib/auth/use-me";
import type { ConversationItem } from "@/types/account";
import type { Conversation } from "@/types/conversation";
import { queryKeys } from "@/lib/query/keys";

import { conversations } from "./conversations";
import { NoContent, request } from "@/lib/backend/request";

import { ChatListChange, chatListChanged, chatListQuery, chatQuery } from "./queries";
import type { ChatPage, ChatRead } from "./read";
import { itemsFromBrowser, itemsFromServer } from "./restore";
import { sessionHeaders } from "./session";
import { useConversations } from "./use-conversations";

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

type Pages = { data: InfiniteData<ChatPage> | undefined; hasNextPage: boolean; isFetching: boolean; isFetchingNextPage: boolean; isPending: boolean; fetchNextPage: () => Promise<unknown> };

function memberList(q: Pages): ChatList {
  const pages = q.data?.pages ?? [];
  const ok = pages.flatMap((p) => (p.ok ? [p] : []));
  const last = pages.at(-1);
  return {
    member: true,
    items: itemsFromServer(ok.flatMap((p) => p.items)),
    more: q.hasNextPage,
    loadMore: () => { if (!q.isFetching) void q.fetchNextPage(); },
    loadingMore: q.isFetchingNextPage,
    moreFailed: ok.length > 0 && last !== undefined && !last.ok,
    loading: q.isPending,
    error: !ok.length && last && !last.ok ? last.message : null,
    browser: [],
  };
}

function guestList(browser: Conversation[]): ChatList {
  return {
    member: false, items: itemsFromBrowser(browser), more: false, loadMore: () => {}, loadingMore: false, moreFailed: false,
    loading: false, error: null, browser,
  };
}

// The conversation list of the sidebar and dialogs: GET /chat for a member, this tab for a guest. Not suspending:
// it picks up a list the server prefetched (/chat, /settings) and otherwise reads it in the browser.
export function useChatList(): ChatList {
  const auth = useAuth();
  const member = auth.status === "member";
  const browser = useConversations();
  const server = useInfiniteQuery({ ...chatListQuery, enabled: member });
  return member ? memberList(server) : guestList(browser);
}

// /chat for a member: the server prefetched the first page (ChatListHydration), so this suspends only until it
// streams in. Use it only under that prefetch: a suspending query on the server cannot reach /api/chats.
export function useMemberChatList(): ChatList {
  return memberList(useSuspenseInfiniteQuery(chatListQuery));
}

export function useGuestChatList(): ChatList {
  return guestList(useConversations());
}

// One member conversation for /chat/[id]. The page prefetches it on the server with the login
// (SavedChatHydration), so this suspends only until that answer streams in.
export function useMemberChat(id: string): ChatRead {
  return useSuspenseQuery(chatQuery(id)).data;
}

// Warm the conversation a list row points to (rendered while the row is hovered or focused): opening it then
// starts from the cache instead of a request.
export function useWarmChat(id: string): void {
  usePrefetchQuery(chatQuery(id));
}

// Delete one conversation: on the server (member: the conversation, guest: its thread) and from this tab.
export function useDeleteChat() {
  const client = useQueryClient();
  const auth = useAuth();
  return useMutation({
    mutationFn: async (c: { id: string; threadId: string | null }) => {
      if (auth.status === "member") {
        const res = await request(`/api/chats/${encodeURIComponent(c.id)}`, { method: "DELETE", responseSchema: NoContent });
        // the screens read mutation.error.message: a failed value is raised inside the mutation only
        if (!res.ok && res.status !== 404) throw new Error(`삭제하지 못했습니다${res.status ? ` (${res.status})` : ""}`);
        return;
      }
      conversations.remove(c.id);
      // the thread's earlier turns on the server; best effort (a guest list lives in this tab either way)
      if (c.threadId) {
        await request(`/api/chats/${encodeURIComponent(c.threadId)}`, { method: "DELETE", headers: sessionHeaders(), responseSchema: NoContent });
      }
    },
    onSuccess: (_, c) => {
      client.removeQueries({ queryKey: queryKeys.chat(c.id), exact: true });
      return chatListChanged(client, ChatListChange.CHAT_DELETED);
    },
  });
}
