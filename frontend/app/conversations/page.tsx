"use client";

import { CircleCheck, MessageSquare, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState, useSyncExternalStore } from "react";

import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { type Conversation, conversations, matchConversations } from "@/lib/conversations";

const EMPTY: Conversation[] = [];

export default function ConversationsPage() {
  const list = useSyncExternalStore(conversations.subscribe, conversations.list, () => EMPTY);
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState<Conversation | null>(null);
  const [deleted, setDeleted] = useState(false);
  const shown = matchConversations(list, query);

  return (
    <AppShell title="대화 목록" sub="이 브라우저에 저장된 대화">
      <div className="mx-auto max-w-4xl space-y-4">
        {deleted && (
          <div role="status" className="flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            <CircleCheck size={18} />대화를 삭제했습니다.
          </div>
        )}
        <div className="flex items-center gap-3">
          <label className="flex h-10 flex-1 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm focus-within:border-brand-500">
            <Search size={16} className="text-mute" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} className="w-full outline-none" placeholder="대화 제목·내용 검색" />
          </label>
          <Button asChild><Link href="/">새 대화</Link></Button>
        </div>
        {shown.length ? (
          <Card className="divide-y divide-line">
            {shown.map((c) => (
              <div key={c.id} className="flex items-center gap-4 px-5 py-4 hover:bg-gray-50/70">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-full border border-brand-100 bg-brand-50 text-brand-600">
                  <MessageSquare size={17} />
                </span>
                <Link href={`/c/${c.id}`} className="min-w-0 flex-1">
                  <p className="truncate text-[15px] font-medium">{c.title}</p>
                  <p className="mt-0.5 text-xs text-mute">
                    {new Date(c.updatedAt).toLocaleString("ko-KR")} · 질문 {c.messages.filter((m) => m.role === "user").length}개
                  </p>
                </Link>
                <Button variant="ghost" size="sm" className="text-mute hover:text-red-600" onClick={() => setPending(c)}>
                  <Trash2 />삭제
                </Button>
              </div>
            ))}
          </Card>
        ) : (
          <Card className="px-5 py-12 text-center text-sm text-sub">
            {list.length ? "일치하는 대화가 없어요." : "아직 저장된 대화가 없어요. 새 대화에서 질문해 보세요."}
          </Card>
        )}
      </div>
      <Dialog open={pending !== null} onOpenChange={(open) => !open && setPending(null)}>
        <DialogContent>
          <DialogTitle>이 대화를 삭제하시겠습니까?</DialogTitle>
          <DialogDescription>‘{pending?.title}’ 대화를 이 브라우저에서 지웁니다. 삭제하면 되돌릴 수 없습니다.</DialogDescription>
          <DialogFooter>
            <DialogClose asChild><Button variant="secondary">취소</Button></DialogClose>
            <Button
              variant="destructive"
              onClick={() => {
                if (pending) conversations.remove(pending.id);
                setPending(null);
                setDeleted(true);
              }}
            >
              삭제
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
