"use client";

import { useMemo } from "react";

import { AppShell } from "@/components/app-shell";
import { conversationFrom } from "@/lib/chat/restore";
import { useMemberChat } from "@/lib/chat/use-chat-list";

import { Chat } from "../chat";
import { MissingChat } from "./missing-chat";

// A member's saved conversation, from the server (GET /chat/{id})
export function MemberSavedChat({ id }: { id: string }) {
  const read = useMemberChat(id);
  const conversation = useMemo(() => (read.status === "ready" ? conversationFrom(read.chat) : null), [read]);
  if (read.status === "missing") {
    return (
      <AppShell title="대화를 찾을 수 없습니다">
        <MissingChat title="계정에 없는 대화입니다" text="삭제했거나 90일 보관 기간이 지난 대화일 수 있어요." />
      </AppShell>
    );
  }
  if (read.status === "error" || !conversation) {
    const message = read.status === "error" ? read.message : "대화를 읽지 못했습니다";
    return (
      <AppShell title="대화를 불러오지 못했습니다">
        <MissingChat title="대화를 불러오지 못했습니다" text={`${message}. 잠시 뒤 다시 시도해 주세요.`} />
      </AppShell>
    );
  }
  return <Chat key={conversation.id} id={id} initial={conversation} />;
}
