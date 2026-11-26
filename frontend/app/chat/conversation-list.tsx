"use client";

import { useState } from "react";

import { LOGIN_PROMPT, LoginPrompt, LoginPromptLayout } from "@/components/account/login-prompt";
import { AppShell } from "@/components/app-shell";
import { DeleteChatDialog } from "@/components/chat/history/delete-chat-dialog";
import { useAuth } from "@/lib/auth/use-me";
import { matchConversations } from "@/lib/chat/conversations";
import { LIST_SUB, showsLoginPrompt } from "@/lib/chat/list-view";
import { itemsFromBrowser } from "@/lib/chat/restore";
import { useGuestChatList, useMemberChatList, type ChatList } from "@/lib/chat/use-chat-list";
import type { ConversationItem } from "@/types/account";

import { DeletedNotice } from "./components/deleted-notice";
import { ListBody } from "./components/list-body";
import { ListToolbar } from "./components/list-toolbar";
import { LoadMore } from "./components/load-more";

// The /chat list. A member's comes from the server prefetch (useSuspenseInfiniteQuery), a guest's from this tab.
// The auth status is read on every render, so a login or logout switches the list without a reload.
export function ConversationList() {
  return useAuth().status === "member" ? <MemberConversations /> : <GuestConversations />;
}

function MemberConversations() {
  return <ListView list={useMemberChatList()} />;
}

function GuestConversations() {
  return <ListView list={useGuestChatList()} />;
}

// A member searches the titles the server sent; a guest's search reads every question and answer of this tab
function shownItems(list: ChatList, query: string): ConversationItem[] {
  if (!list.member) return itemsFromBrowser(matchConversations(list.browser, query));
  const q = query.trim().toLowerCase();
  return list.items.filter((c) => !q || c.title.toLowerCase().includes(q));
}

function ListView({ list }: { list: ChatList }) {
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState<ConversationItem | null>(null);
  const [deleted, setDeleted] = useState(false);
  const shown = shownItems(list, query);

  function handleQuery(q: string) {
    setQuery(q);
    setDeleted(false);
  }

  function handleDelete(item: ConversationItem) {
    setDeleted(false);
    setPending(item);
  }

  function handleDialogClose() {
    setPending(null);
  }

  function handleDeleted() {
    setDeleted(true);
  }

  return (
    <AppShell title="대화 목록" sub={list.member ? LIST_SUB.member : LIST_SUB.guest}>
      <div className="mx-auto max-w-4xl space-y-4">
        {deleted && <DeletedNotice />}
        <ListToolbar query={query} member={list.member} onQuery={handleQuery} />
        <ListBody list={list} shown={shown} onDelete={handleDelete} />
        {list.more && <LoadMore list={list} />}
        {showsLoginPrompt(list) && <LoginPrompt layout={LoginPromptLayout.INLINE} message={LOGIN_PROMPT.ANY_DEVICE} />}
      </div>
      <DeleteChatDialog item={pending} member={list.member} onClose={handleDialogClose} onDeleted={handleDeleted} />
    </AppShell>
  );
}
