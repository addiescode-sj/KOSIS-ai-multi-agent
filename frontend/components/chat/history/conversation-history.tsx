"use client";

import { ArrowRight, Cloud, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { formatDay, groupByDay } from "@/lib/chat/history";
import { useChatList } from "@/lib/chat/use-chat-list";
import type { ConversationItem } from "@/types/account";

import { DeleteChatDialog } from "./delete-chat-dialog";

const SHOWN = 20;

// The chat screen's left card (prototype Chat History): a member's conversations grouped by day with the count
// saved to the account, or this browser's conversations for a guest.
export function ConversationHistory({ activeId }: { activeId: string }) {
  const list = useChatList();
  const [pending, setPending] = useState<ConversationItem | null>(null);
  const [now] = useState(Date.now); // day groups as of opening the screen
  const items = list.items.slice(0, SHOWN);
  const groups = list.member ? groupByDay(items, now) : [{ label: null, items }];

  return (
    <Card className="sticky top-[96px] hidden h-fit max-h-[calc(100dvh-120px)] w-72 shrink-0 flex-col overflow-y-auto p-4 xl:flex">
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- full load: see the "새 대화" link in app-shell */}
      <Button asChild className="w-full"><a href="/">새 대화</a></Button>
      {list.member ? (
        <div className="mt-5 flex items-center justify-between px-1">
          <p className="text-[13px] font-semibold">내 대화</p>
          <span className="inline-flex items-center gap-1 text-[11px] text-mute">
            <Cloud size={12} />계정에 저장됨 · {list.items.length}{list.more ? "+" : ""}개
          </span>
        </div>
      ) : (
        <p className="mt-5 mb-2 px-1 text-[11px] font-semibold tracking-wider text-mute uppercase">이전 대화</p>
      )}
      {list.error && <p className="mt-3 px-1 text-[13px] text-red-600">{list.error}</p>}
      {!list.loading && !list.error && list.items.length === 0 && <p className="mt-3 px-1 text-[13px] text-sub">아직 저장된 대화가 없어요.</p>}
      {groups.map((g) => (
        <div key={g.label ?? "all"}>
          {g.label && <p className="mt-4 mb-1 px-3 text-[11px] font-semibold text-mute">{g.label}</p>}
          <ul className="space-y-1">
            {g.items.map((it) => {
              const active = it.id === activeId;
              return (
                <li key={it.id} className={cn("group flex items-center gap-2 rounded-lg px-3 py-2.5", active ? "bg-brand-50" : "hover:bg-gray-50")}>
                  <Link href={`/chat/${it.id}`} aria-current={active ? "page" : undefined} className="min-w-0 flex-1">
                    <p className={cn("truncate text-sm", active ? "font-semibold text-brand-700" : "text-ink")}>{it.title}</p>
                    <p className="text-xs text-mute">{formatDay(it.updatedAt)}{it.questions !== null && ` · 질문 ${it.questions}개`}</p>
                  </Link>
                  <button
                    type="button"
                    onClick={() => setPending(it)}
                    aria-label={`‘${it.title}’ 삭제`}
                    className="cursor-pointer rounded p-1 text-mute opacity-0 group-hover:opacity-100 hover:bg-white hover:text-red-500 focus-visible:opacity-100"
                  >
                    <Trash2 size={15} />
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <Link href="/chat" className="mt-4 inline-flex items-center justify-center gap-1 border-t border-line pt-3 text-[13px] font-medium text-brand-600 hover:underline">
        전체 대화 목록 보기 <ArrowRight size={14} />
      </Link>
      <DeleteChatDialog item={pending} member={list.member} onClose={() => setPending(null)} activeId={activeId} />
    </Card>
  );
}
