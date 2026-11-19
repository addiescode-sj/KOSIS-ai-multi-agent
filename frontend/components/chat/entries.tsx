"use client";

import { MessageSquareOff } from "lucide-react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { use, useState, useSyncExternalStore } from "react";

import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { conversations } from "@/lib/conversations";

import { Chat } from "./chat";

// `/`: a new conversation. `?q=` prefills
// the question box (e.g. "이 표로 질문하기" on the search page).
export function NewChat() {
  const prefill = useSearchParams().get("q") ?? undefined;
  // useSearchParams keeps this component out of the prerender, so the id is made in the browser
  const [id] = useState(() => crypto.randomUUID());
  return <Chat key={id} id={id} initial={null} prefill={prefill} />;
}

// `/c/[id]`: a conversation saved in this browser, shown as it was (results, charts, sources) and continued.
export function SavedChat({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const conversation = useSyncExternalStore(
    conversations.subscribe,
    () => conversations.get(id),
    () => undefined, // server render: not read yet
  );
  if (conversation === undefined) return null;
  if (conversation === null) {
    return (
      <AppShell title="대화를 찾을 수 없습니다">
        <Card className="mx-auto flex max-w-lg flex-col items-center p-8 text-center">
          <span className="flex size-14 items-center justify-center rounded-full bg-canvas text-mute"><MessageSquareOff size={26} /></span>
          <h2 className="mt-5 text-lg font-semibold">이 브라우저에 저장된 대화가 아닙니다</h2>
          <p className="mt-1.5 text-sm text-sub">대화는 질문한 브라우저에만 저장됩니다. 삭제했거나 다른 기기에서 나눈 대화일 수 있어요.</p>
          <Button asChild className="mt-6"><Link href="/">새 대화 시작</Link></Button>
        </Card>
      </AppShell>
    );
  }
  return <Chat id={id} initial={conversation} />;
}
