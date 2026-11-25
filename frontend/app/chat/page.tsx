import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { Card } from "@/components/ui/card";

import { ChatListHydration } from "./chat-list-hydration";
import { ConversationList } from "./conversation-list";

// `/chat`: a member's conversations from the account (GET /chat, prefetched on the server and streamed in), or a
// guest's saved in this tab.
export default function Page() {
  return (
    <Suspense
      fallback={
        <AppShell title="대화 목록">
          <Card className="mx-auto max-w-4xl px-5 py-12 text-center text-sm text-sub">대화 목록을 불러오는 중입니다…</Card>
        </AppShell>
      }
    >
      <ChatListHydration>
        <ConversationList />
      </ChatListHydration>
    </Suspense>
  );
}
