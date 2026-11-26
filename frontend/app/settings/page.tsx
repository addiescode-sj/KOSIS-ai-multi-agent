import { Suspense } from "react";

import { ChatListHydration } from "@/app/chat/chat-list-hydration";
import { SettingsScreen } from "@/components/settings/settings-screen";

// The withdrawal dialog counts the member's saved conversations: the list starts on the server (streamed in)
export default function Page() {
  return (
    <Suspense>
      <ChatListHydration>
        <SettingsScreen />
      </ChatListHydration>
    </Suspense>
  );
}
