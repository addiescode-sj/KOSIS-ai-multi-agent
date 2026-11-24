"use client";

import { AppShell } from "@/components/app-shell";
import { useConversation } from "@/lib/chat/use-conversations";

import { Chat } from "../chat";
import { MissingChat } from "./missing-chat";

// A guest's saved conversation, from this browser (localStorage: not known in the server render)
export function GuestSavedChat({ id }: { id: string }) {
  const local = useConversation(id);
  if (local === undefined) return <AppShell title="대화" sub="저장된 대화를 읽는 중"><div /></AppShell>;
  if (local === null) {
    return (
      <AppShell title="대화를 찾을 수 없습니다">
        <MissingChat
          title="이 브라우저에 저장된 대화가 아닙니다"
          text="대화는 질문한 브라우저에만 저장됩니다. 삭제했거나 다른 기기에서 나눈 대화일 수 있어요. 로그인하면 어느 기기에서나 볼 수 있어요."
        />
      </AppShell>
    );
  }
  return <Chat key={local.id} id={id} initial={local} />;
}
