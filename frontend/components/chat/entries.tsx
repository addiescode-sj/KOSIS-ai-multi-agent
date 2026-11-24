"use client";

import { MessageSquareOff } from "lucide-react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { use, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useSavedConversation } from "@/lib/chat/use-chat-list";

import { Chat } from "./chat";

// `/`: a new conversation. `?q=` prefills
// the question box (e.g. "이 표로 질문하기" on the search page).
export function NewChat() {
  const prefill = useSearchParams().get("q") ?? undefined;
  // useSearchParams keeps this component out of the prerender, so the id is made in the browser
  const [id] = useState(() => crypto.randomUUID());
  return <Chat key={id} id={id} initial={null} prefill={prefill} />;
}

// `/chat/[id]`: a saved conversation, shown as it was (results, charts, sources) and continued. A member's comes
// from the server (GET /chat/{id}), a guest's from this browser.
export function SavedChat({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const saved = useSavedConversation(id);
  if (saved.status === "loading") return <AppShell title="대화" sub="저장된 대화를 읽는 중"><div /></AppShell>; // server render: not read yet
  if (saved.status === "error") {
    return (
      <AppShell title="대화를 불러오지 못했습니다">
        <Missing title="대화를 불러오지 못했습니다" text={`${saved.message}. 잠시 뒤 다시 시도해 주세요.`} />
      </AppShell>
    );
  }
  if (saved.status === "missing") {
    return (
      <AppShell title="대화를 찾을 수 없습니다">
        {saved.member ? (
          <Missing title="계정에 없는 대화입니다" text="삭제했거나 90일 보관 기간이 지난 대화일 수 있어요." />
        ) : (
          <Missing title="이 브라우저에 저장된 대화가 아닙니다" text="대화는 질문한 브라우저에만 저장됩니다. 삭제했거나 다른 기기에서 나눈 대화일 수 있어요. 로그인하면 어느 기기에서나 볼 수 있어요." />
        )}
      </AppShell>
    );
  }
  return <Chat key={saved.conversation.id} id={id} initial={saved.conversation} />;
}

function Missing({ title, text }: { title: string; text: string }) {
  return (
    <Card className="mx-auto flex max-w-lg flex-col items-center p-8 text-center">
      <span className="flex size-14 items-center justify-center rounded-full bg-canvas text-mute"><MessageSquareOff size={26} /></span>
      <h2 className="mt-5 text-lg font-semibold">{title}</h2>
      <p className="mt-1.5 text-sm text-sub">{text}</p>
      <Button asChild className="mt-6"><Link href="/">새 대화 시작</Link></Button>
    </Card>
  );
}
