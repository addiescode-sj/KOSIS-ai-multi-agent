"use client";

import { CircleCheck, MessageSquare, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { KakaoLoginButton } from "@/components/account/kakao-login-button";
import { AppShell } from "@/components/app-shell";
import { DeleteChatDialog } from "@/components/chat/history/delete-chat-dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { matchConversations } from "@/lib/chat/conversations";
import { formatDay } from "@/lib/chat/history";
import { itemsFromBrowser } from "@/lib/chat/restore";
import { useChatList } from "@/lib/chat/use-chat-list";
import type { ConversationItem } from "@/types/account";

// `/chat`: a member's conversations from the account (GET /chat), or a guest's saved in this browser.

export default function ConversationsPage() {
  const list = useChatList();
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState<ConversationItem | null>(null);
  const [deleted, setDeleted] = useState(false);
  const q = query.trim().toLowerCase();
  // a guest's search reads every question and answer; the server list has titles only
  const shown = list.member
    ? list.items.filter((c) => !q || c.title.toLowerCase().includes(q))
    : itemsFromBrowser(matchConversations(list.browser, query));

  return (
    <AppShell title="대화 목록" sub={list.member ? "카카오 계정에 저장된 대화" : "이 브라우저에 저장된 비회원 대화"}>
      <div className="mx-auto max-w-4xl space-y-4">
        {deleted && (
          <div role="status" className="flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            <CircleCheck size={18} />대화를 삭제했습니다.
          </div>
        )}
        <div className="flex items-center gap-3">
          <label className="flex h-10 flex-1 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm focus-within:border-brand-500">
            <Search size={16} className="text-mute" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} className="w-full outline-none" placeholder={list.member ? "대화 제목 검색" : "대화 제목·내용 검색"} />
          </label>
          {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- full load: see the "새 대화" link in app-shell */}
          <Button asChild><a href="/">새 대화</a></Button>
        </div>
        {list.error ? (
          <Card className="px-5 py-12 text-center text-sm text-red-600">{list.error}</Card>
        ) : list.loading ? (
          <Card className="px-5 py-12 text-center text-sm text-sub">대화 목록을 불러오는 중입니다…</Card>
        ) : shown.length ? (
          <Card className="divide-y divide-line">
            {shown.map((c) => (
              <div key={c.id} className="flex items-center gap-4 px-5 py-4 hover:bg-gray-50/70">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-full border border-brand-100 bg-brand-50 text-brand-600">
                  <MessageSquare size={17} />
                </span>
                <Link href={`/chat/${c.id}`} className="min-w-0 flex-1">
                  <p className="truncate text-[15px] font-medium">{c.title}</p>
                  <p className="mt-0.5 text-xs text-mute">{formatDay(c.updatedAt)} · 질문 {c.questions}개</p>
                </Link>
                <Button variant="ghost" size="sm" className="text-mute hover:text-red-600" onClick={() => setPending(c)}>
                  <Trash2 />삭제
                </Button>
              </div>
            ))}
          </Card>
        ) : (
          <Card className="px-5 py-12 text-center text-sm text-sub">
            {list.items.length ? "일치하는 대화가 없어요." : "아직 저장된 대화가 없어요. 새 대화에서 질문해 보세요."}
          </Card>
        )}
        {!list.member && !list.loading && (
          <div className="flex flex-col items-center gap-3 pt-2 text-center text-[13px] text-sub">
            로그인하면 어느 기기에서나 대화를 볼 수 있어요.
            <KakaoLoginButton size="sm" />
          </div>
        )}
      </div>
      <DeleteChatDialog item={pending} member={list.member} onClose={() => setPending(null)} onDeleted={() => setDeleted(true)} />
    </AppShell>
  );
}
